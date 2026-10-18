"""Massive (formerly Polygon.io) client for real market data.

See planning/MASSIVE_API.md. Two modes, detected on the first poll:

- LIVE: poll the full-market snapshot endpoint (realtime or 15-minute delayed,
  depending on the plan).
- EOD: the plan lacks the snapshot endpoint (free Basic plan), so prices are the
  last close from grouped daily bars, refreshed once per day.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

from massive import RESTClient
from massive.exceptions import AuthError
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource
from .models import Quote, QuoteUnavailable, SourceKind, SourceStatus, UnknownTicker
from .tickers import normalize_ticker
from .timeutil import to_unix_seconds

logger = logging.getLogger(__name__)

REALTIME_POLL_INTERVAL = 5.0
DELAYED_POLL_INTERVAL = 15.0
EOD_RECHECK_INTERVAL = 3600.0  # re-evaluate hourly; fetch only when the date changed


class Mode(enum.Enum):
    LIVE = "live"
    EOD = "eod"


class NotAuthorized(Exception):
    """HTTP 403: the plan does not include this endpoint."""


class BadKey(Exception):
    """HTTP 401: invalid or missing API key."""


def classify_error(exc: Exception) -> Exception:
    """Map an SDK exception to NotAuthorized / BadKey, or return it unchanged."""
    if isinstance(exc, AuthError):
        return BadKey(str(exc))
    text = str(exc).lower()
    if "not_authorized" in text or "not entitled" in text or "upgrade your plan" in text:
        return NotAuthorized(str(exc))
    if "unknown api key" in text or "invalid api key" in text:
        return BadKey(str(exc))
    return exc


def pick_price(snap) -> tuple[float, float | None] | None:
    """Choose a price from a snapshot: last trade > minute close > day close > prev close.

    Returns (price, as_of_unix_seconds | None) or None when nothing usable exists.
    """

    def good(value) -> bool:
        return isinstance(value, (int, float)) and value > 0 and value == value

    last_trade = getattr(snap, "last_trade", None)
    if last_trade is not None and good(getattr(last_trade, "price", None)):
        return last_trade.price, to_unix_seconds(getattr(last_trade, "sip_timestamp", None))
    minute = getattr(snap, "min", None)
    if minute is not None and good(getattr(minute, "close", None)):
        return minute.close, to_unix_seconds(getattr(minute, "timestamp", None))
    day = getattr(snap, "day", None)
    if day is not None and good(getattr(day, "close", None)):
        return day.close, to_unix_seconds(getattr(snap, "updated", None))
    prev = getattr(snap, "prev_day", None)
    if prev is not None and good(getattr(prev, "close", None)):
        return prev.close, to_unix_seconds(getattr(prev, "timestamp", None))
    return None


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive REST API."""

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float | None = None,
        plan: str = "delayed",
        client=None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._plan = "realtime" if plan == "realtime" else "delayed"
        self._interval = poll_interval or (
            REALTIME_POLL_INTERVAL if self._plan == "realtime" else DELAYED_POLL_INTERVAL
        )
        self._clock = clock
        self._client: RESTClient | None = client
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._mode = Mode.LIVE
        self._eod_date: date | None = None  # date we last fetched EOD for
        self.status = SourceStatus.STARTING

    @property
    def kind(self) -> SourceKind:  # type: ignore[override]
        if self._mode is Mode.EOD:
            return SourceKind.MASSIVE_EOD
        return (
            SourceKind.MASSIVE_REALTIME if self._plan == "realtime" else SourceKind.MASSIVE_DELAYED
        )

    # --- Lifecycle ---

    async def start(self, tickers: list[str]) -> None:
        if self._client is None:
            self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(dict.fromkeys(tickers))
        await self._poll_once()
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info("Massive poller started: %d tickers, mode=%s", len(tickers), self._mode.value)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._tickers and ticker in self._cache:
            return
        quotes = await self.get_quotes([ticker])
        quote = quotes.get(ticker)
        if quote is None:
            raise UnknownTicker(f"Massive has no price for {ticker}")
        self._cache.update(
            ticker, quote.price, as_of=quote.as_of, prev_close=quote.prev_close, source=self.kind
        )
        if ticker not in self._tickers:
            self._tickers.append(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- One-shot queries ---

    async def get_quotes(self, tickers: list[str]) -> dict[str, Quote]:
        tickers = list(dict.fromkeys(t.upper().strip() for t in tickers))
        if not tickers or self._client is None:
            return {}
        if self._mode is Mode.LIVE:
            try:
                snaps = await asyncio.to_thread(self._fetch_snapshots, tickers)
                return self._quotes_from_snapshots(snaps)
            except Exception as exc:  # noqa: BLE001
                err = classify_error(exc)
                if not isinstance(err, NotAuthorized):
                    raise QuoteUnavailable(str(err)) from exc
                self._mode = Mode.EOD
        return await self._eod_quotes(tickers)

    async def get_eod_closes(self, tickers: list[str], on: date | None = None) -> dict[str, float]:
        wanted = {t.upper().strip() for t in tickers}
        if self._client is None or not wanted:
            return {}
        if on is not None:
            return await asyncio.to_thread(self._grouped_closes, on, wanted)
        _, closes = await asyncio.to_thread(self._latest_closes, wanted, self._today())
        return closes

    # --- Polling ---

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(EOD_RECHECK_INTERVAL if self._mode is Mode.EOD else self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        if not self._tickers or self._client is None:
            if self.status is SourceStatus.STARTING:
                self.status = SourceStatus.OK
            return
        if self._mode is Mode.LIVE:
            try:
                snaps = await asyncio.to_thread(self._fetch_snapshots, self._tickers)
            except Exception as exc:  # noqa: BLE001
                err = classify_error(exc)
                if isinstance(err, NotAuthorized):
                    logger.warning("Massive plan lacks the snapshot endpoint; using EOD mode")
                    self._mode = Mode.EOD
                elif isinstance(err, BadKey):
                    logger.error("Massive rejected the API key: %s", err)
                    self.status = SourceStatus.ERROR
                    return
                else:
                    logger.error("Massive poll failed: %s", err)
                    self.status = SourceStatus.DEGRADED
                    return
            else:
                for ticker, quote in self._quotes_from_snapshots(snaps).items():
                    self._cache.update(
                        ticker,
                        quote.price,
                        as_of=quote.as_of,
                        prev_close=quote.prev_close,
                        source=self.kind,
                    )
                self.status = SourceStatus.OK
                return
        await self._poll_eod()

    async def _poll_eod(self) -> None:
        today = self._today()
        if self._eod_date == today and all(t in self._cache for t in self._tickers):
            return
        try:
            quotes = await self._eod_quotes(self._tickers)
        except Exception as exc:  # noqa: BLE001
            err = classify_error(exc)
            logger.error("Massive EOD fetch failed: %s", err)
            self.status = SourceStatus.ERROR if isinstance(err, BadKey) else SourceStatus.DEGRADED
            return
        for ticker, quote in quotes.items():
            self._cache.update(
                ticker,
                quote.price,
                as_of=quote.as_of,
                prev_close=quote.prev_close,
                source=SourceKind.MASSIVE_EOD,
            )
        self._eod_date = today
        self.status = SourceStatus.OK

    # --- Internals (synchronous SDK calls run in a worker thread) ---

    def _today(self) -> date:
        return datetime.fromtimestamp(self._clock()).date()

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        return self._client.get_snapshot_all(  # type: ignore[union-attr]
            market_type=SnapshotMarketType.STOCKS, tickers=list(tickers)
        )

    def _quotes_from_snapshots(self, snaps) -> dict[str, Quote]:
        now = self._clock()
        out: dict[str, Quote] = {}
        for snap in snaps:
            ticker = getattr(snap, "ticker", None)
            picked = pick_price(snap)
            if not ticker or picked is None:
                logger.warning("Skipping snapshot for %s: no usable price", ticker)
                continue
            price, as_of = picked
            prev = getattr(snap, "prev_day", None)
            prev_close = getattr(prev, "close", None) if prev is not None else None
            if not (isinstance(prev_close, (int, float)) and prev_close > 0):
                prev_close = None
            out[ticker] = Quote(
                ticker,
                round(price, 2),
                round(price, 2),
                round(prev_close, 2) if prev_close else None,
                as_of or now,
                now,
                self.kind,
            )
        return out

    def _grouped_closes(self, on: date, wanted: set[str]) -> dict[str, float]:
        bars = self._client.get_grouped_daily_aggs(on, adjusted=True)  # type: ignore[union-attr]
        return {
            b.ticker: b.close
            for b in bars
            if b.ticker in wanted and isinstance(b.close, (int, float)) and b.close > 0
        }

    def _latest_closes(
        self, wanted: set[str], before: date
    ) -> tuple[date | None, dict[str, float]]:
        """Walk back from the day before `before` until a trading day with bars is found."""
        day = before
        for _ in range(10):
            day -= timedelta(days=1)
            if day.weekday() >= 5:  # weekends never trade
                continue
            closes = self._grouped_closes(day, wanted)
            if closes:
                return day, closes
        return None, {}

    async def _eod_quotes(self, tickers: list[str]) -> dict[str, Quote]:
        wanted = set(tickers)
        today = self._today()
        day, closes = await asyncio.to_thread(self._latest_closes, wanted, today)
        if day is None:
            return {}
        prev_day, prev_closes = await asyncio.to_thread(self._latest_closes, wanted, day)
        now = self._clock()
        as_of = datetime(day.year, day.month, day.day, 21, 0).timestamp()
        return {
            t: Quote(
                t,
                round(c, 2),
                round(c, 2),
                round(prev_closes[t], 2) if t in prev_closes else None,
                as_of,
                now,
                SourceKind.MASSIVE_EOD,
            )
            for t, c in closes.items()
        }
