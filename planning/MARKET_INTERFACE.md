# Market Data Interface

The unified Python API for stock prices in FinAlly. Callers get the same types and methods whether prices come from the Massive API (when `MASSIVE_API_KEY` is set and non-empty) or from the built-in simulator (otherwise). Nothing outside `backend/app/market/` imports `massive` or the simulator directly.

Companion docs: [MASSIVE_API.md](MASSIVE_API.md) (provider details and plan limits), [MARKET_SIMULATOR.md](MARKET_SIMULATOR.md) (simulator internals). This revises the earlier interface in `archive/MARKET_INTERFACE.md` to fix the gaps found in `REVIEW.md` (daily baseline, freshness, free-tier EOD mode, timestamp units).

## 1. Goals

1. One import, one factory, source chosen by environment.
2. Downstream code (SSE, portfolio valuation, trading) reads a shared in-memory cache and never calls a provider directly.
3. Every quote says **how trustworthy it is**: its source, when it was priced, and whether it is stale. A missing price is `None`, never `0`.
4. Both a streaming path (cache + SSE) and a one-shot path (`get_quotes`, `get_eod_closes`) for multiple tickers.

## 2. Module layout

```
backend/app/market/
├── __init__.py        # public exports: Quote, PriceCache, MarketDataSource, create_market_data_source
├── models.py          # Quote, SourceKind, SourceStatus
├── cache.py           # PriceCache (thread-safe, versioned)
├── interface.py       # MarketDataSource ABC
├── factory.py         # create_market_data_source()
├── simulator.py       # GBMSimulator + SimulatorDataSource   (see MARKET_SIMULATOR.md)
├── seed_prices.py     # seeds, GBM params, correlation groups
├── massive_client.py  # MassiveDataSource (snapshot poller + EOD fallback)
├── timeutil.py        # to_unix_seconds()
└── stream.py          # SSE router
```

## 3. Data model

```python
# models.py
from __future__ import annotations
import math, time
from dataclasses import dataclass
from enum import Enum

class SourceKind(str, Enum):
    SIMULATOR = "simulator"
    MASSIVE_REALTIME = "massive-realtime"
    MASSIVE_DELAYED = "massive-delayed"     # ~15 minutes old
    MASSIVE_EOD = "massive-eod"             # last close only (free plan)

@dataclass(frozen=True, slots=True)
class Quote:
    ticker: str
    price: float                 # positive, finite
    prev_price: float            # previous tick (drives the green/red flash)
    prev_close: float | None     # daily-change baseline; None if unknown
    as_of: float                 # unix SECONDS when the price was struck (provider/trade time)
    fetched_at: float            # unix seconds when we stored it
    source: SourceKind

    @property
    def direction(self) -> str:
        return "up" if self.price > self.prev_price else "down" if self.price < self.prev_price else "flat"

    @property
    def day_change(self) -> float | None:
        return None if self.prev_close is None else round(self.price - self.prev_close, 4)

    @property
    def day_change_pct(self) -> float | None:
        if not self.prev_close:
            return None
        return round((self.price - self.prev_close) / self.prev_close * 100, 4)

    def age(self, now: float | None = None) -> float:
        return (now or time.time()) - self.as_of

    def to_dict(self) -> dict: ...   # all fields + direction, day_change, day_change_pct; seconds as floats
```

Two percentages exist on purpose: `direction` compares with the previous tick (flash animation); `day_change_pct` compares with `prev_close` (watchlist column). The earlier `change_percent` mixed the two up.

`prev_close` meaning: the simulator uses the first price seen since server start (label it "since start" in the UI); Massive uses `prev_day.close` / the prior session's grouped-daily close.

```python
class SourceStatus(str, Enum):
    STARTING = "starting"     # no successful fetch yet
    OK = "ok"
    DEGRADED = "degraded"     # last fetch failed, serving older quotes
    ERROR = "error"           # unrecoverable, e.g. 401 bad key
```

Validation at the cache boundary: reject `price` that is `None`, `NaN`, infinite or `<= 0`; log and keep the old quote.

## 4. PriceCache

Same role as today (thread-safe dict plus version counter) with these changes:

```python
class PriceCache:
    def update(self, ticker, price, *, as_of=None, prev_close=None, source) -> Quote | None
    def get(self, ticker) -> Quote | None
    def get_all(self) -> dict[str, Quote]
    def get_fresh(self, ticker, max_age: float) -> Quote | None   # None if missing or older than max_age
    def remove(self, ticker) -> None                              # also bumps version
    def set_prev_close(self, ticker, prev_close) -> None          # baseline survives later updates
    @property version(self) -> int                                # bumped on update AND remove
```

- `prev_close` is stored per ticker and carried forward on every `update`; it is not discarded.
- `as_of` defaults to `time.time()` for the simulator; Massive passes the converted trade time.
- Use `get_fresh` for trading and valuation, never bare `get`. Freshness limits: simulator 5 s; Massive realtime 60 s; Massive delayed 20 min; Massive EOD never expires within the session day but valuation labels it "last close". Limits live in constants, not scattered.

## 5. The source interface

```python
# interface.py
from abc import ABC, abstractmethod
from datetime import date

class MarketDataSource(ABC):
    kind: SourceKind                       # set after start() for Massive (depends on plan detection)
    status: SourceStatus

    # --- streaming path: writes to the PriceCache on its own schedule ---
    @abstractmethod
    async def start(self, tickers: list[str]) -> None: ...      # once; first fetch completes before returning
    @abstractmethod
    async def stop(self) -> None: ...                           # idempotent
    @abstractmethod
    async def add_ticker(self, ticker: str) -> None: ...        # must obtain a first quote, or raise
    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None: ...
    @abstractmethod
    def get_tickers(self) -> list[str]: ...

    # --- one-shot path for many tickers, independent of the tracked set ---
    @abstractmethod
    async def get_quotes(self, tickers: list[str]) -> dict[str, Quote]:
        """Latest quote per ticker. Tickers that cannot be priced are omitted (never zero)."""
    @abstractmethod
    async def get_eod_closes(self, tickers: list[str], on: date | None = None) -> dict[str, float]:
        """Close for `on`, default = most recent completed trading day."""
```

Rules every implementation follows:

- `add_ticker` returns only once the ticker has a cache entry (Massive: do an immediate one-shot fetch for it), or raises `UnknownTicker` / `QuoteUnavailable`. This closes the "supported ticker has no quote yet" hole.
- `remove_ticker` is only called by the tracked-set owner when the ticker is in neither the watchlist nor open positions. The source does not decide this.
- Unknown-ticker policy is a product decision, kept out of the sources. See section 9.
- Network or provider failures never raise out of the background loop; they set `status = DEGRADED` and log.

## 6. Factory

```python
# factory.py
def create_market_data_source(cache: PriceCache) -> MarketDataSource:
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if key:
        return MassiveDataSource(api_key=key, cache=cache)
    return SimulatorDataSource(cache=cache)
```

Selection is only by the environment variable. Whitespace-only counts as unset. The decision is logged once at startup, and `GET /api/health` reports `{"market_source": "<kind>", "market_status": "<status>"}`.

## 7. Massive implementation

`MassiveDataSource` (details in [MASSIVE_API.md](MASSIVE_API.md)):

```python
class MassiveDataSource(MarketDataSource):
    def __init__(self, api_key, cache, poll_interval=None): ...

    async def start(self, tickers):
        self._client = RESTClient(api_key=self._key)
        self._tickers = set(tickers)
        await self._poll_once()                       # decides realtime / delayed / EOD mode
        self._task = asyncio.create_task(self._loop())

    async def _poll_once(self):
        if self._mode is not Mode.EOD:
            try:
                snaps = await asyncio.to_thread(self._fetch_snapshots)
            except NotAuthorized:                    # HTTP 403: plan lacks the snapshot endpoint
                self._mode = Mode.EOD
            except BadKey:                           # HTTP 401
                self.status = SourceStatus.ERROR; return
            except Exception:
                self.status = SourceStatus.DEGRADED; return
            else:
                self._apply_snapshots(snaps); return
        await self._poll_eod()                       # grouped daily, rate-limit friendly
```

Behaviour:

- **Snapshot mode.** Per ticker pick the price using the order in MASSIVE_API.md §3 (`last_trade.price` → `min.close` → `day.close` → `prev_day.close`). `as_of` from `last_trade.sip_timestamp` (ns) via `to_unix_seconds`; for the fallbacks use `min.timestamp` (ms) or `updated` (ns). `prev_close = prev_day.close`.
- **Delayed vs realtime.** The API does not label this per response. Take it from configuration: env `MASSIVE_PLAN=realtime|delayed` (default `delayed`, the safe label), not inferred.
- **EOD mode (free plan).** One grouped-daily call at start and once per day; every ticker gets `price = close`, `prev_close = previous session close` (a second grouped call for the day before), `source = MASSIVE_EOD`. Poll interval for the quote cache is irrelevant; the SSE stream simply re-sends the same values. The frontend shows an "end-of-day prices" banner from `/api/health`.
- **Poll interval.** Realtime 2–5 s, delayed 15 s, EOD refreshed daily. Cap in-flight requests to one.
- **Tickers.** Dedupe and uppercase; chunk the `tickers=` parameter if the URL would exceed ~8 KB (about 1,000 symbols; not a concern for 10–50 tickers).
- **Tick direction.** Massive prices only change when a new trade prints. `prev_price` is the previously cached price; if the price is unchanged, direction is `flat`. Do not synthesise movement.

## 8. Consumers

```python
cache = PriceCache()
source = create_market_data_source(cache)
await source.start(sorted(watchlist | held_tickers))      # tracked set = watchlist ∪ positions

# Trading and valuation (never use stale or missing data)
q = cache.get_fresh("AAPL", max_age=FRESH_LIMIT[source.kind])
if q is None:
    raise QuoteUnavailable("AAPL")

# Multi-ticker one-shot lookup, e.g. a first trade in an untracked symbol
quotes = await source.get_quotes(["PYPL", "AAPL"])

# Previous close for a table of tickers
closes = await source.get_eod_closes(["AAPL", "MSFT"])

# Tracked-set changes
await source.add_ticker("TSLA")
await source.remove_ticker("GOOGL")
```

### SSE payload

The stream stays a full snapshot of the cache whenever `cache.version` changes, keyed by ticker, all times in unix **seconds**:

```json
{"AAPL": {"ticker": "AAPL", "price": 190.52, "prev_price": 190.49, "prev_close": 189.1,
          "day_change": 1.42, "day_change_pct": 0.751, "direction": "up",
          "as_of": 1760649600.5, "source": "simulator"}}
```

Behaviour to implement and test: send an initial snapshot immediately on each (re)connect; send an empty object `{}` when the cache becomes empty so clients can clear; removal bumps the cache version; clients de-duplicate chart points whose `as_of` has not changed. Include `source` so the UI can render the delayed/EOD banner. The full REST/SSE contract lives with the API plan; this section only fixes the market-owned part.

## 9. Open decisions

1. **Ticker universe.** PLAN currently says unknown tickers are rejected, but the simulator accepts anything and the seeds cover only the 10 defaults. Pick one: (a) allowlist of the 10 seeded symbols plus a broader curated list with seeds, or (b) accept any symbol, giving the simulator a random seed price (as it does today) and letting Massive reject what it cannot price. Whichever is chosen belongs in one shared `validate_ticker()` used by manual and chat paths.
2. **Plan label.** `MASSIVE_PLAN` env var versus an auto-detection heuristic.
3. **EOD mode trading.** Allow trades at last close (simple) or disable trading with a clear message. Recommend allowing, with the banner.

## 10. Tests

- Factory: unset, empty and whitespace key → simulator; non-empty → Massive.
- Cache: rejects zero/NaN/negative prices; `prev_close` survives updates; `remove` bumps version; `get_fresh` honours age.
- Quote: direction vs. `day_change_pct` use different baselines (fixture where they differ in sign).
- Massive: fixtures from SDK `from_dict` (not `MagicMock`) covering each price fallback, missing `lastTrade`, ns vs ms timestamps; 403 → EOD mode; 401 → `ERROR`; failed poll after success → `DEGRADED` and old quote retained.
- Contract: parametrise one suite over both sources (start/stop idempotence, `add_ticker` yields a cache entry, `get_quotes` omits unpriceable tickers).
- SSE: real generator yields initial snapshot on connect and `{}` after the last removal.
