"""Shared fixtures: an app wired to a deterministic fake market source and a temp database."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.market import (
    MarketDataSource,
    PriceCache,
    QuoteUnavailable,
    SourceKind,
    SourceStatus,
    UnknownTicker,
)

PRICES = {
    "AAPL": 100.0,
    "GOOGL": 175.0,
    "MSFT": 420.0,
    "AMZN": 185.0,
    "TSLA": 250.0,
    "NVDA": 800.0,
    "META": 500.0,
    "JPM": 195.0,
    "V": 280.0,
    "NFLX": 600.0,
    "PYPL": 65.0,
}


class FakeSource(MarketDataSource):
    """Static prices; EOD kind so quotes never go stale. Unknown tickers are rejected."""

    kind = SourceKind.MASSIVE_EOD

    def __init__(self, cache: PriceCache, prices: dict[str, float] | None = None) -> None:
        self.cache = cache
        self.prices = dict(prices or PRICES)
        self.status = SourceStatus.OK
        self._tickers: list[str] = []
        self.refresh_on_add = True

    def set_price(self, ticker: str, price: float) -> None:
        self.prices[ticker] = price
        if ticker in self._tickers:
            self.cache.update(ticker, price, prev_close=self.prices[ticker])

    async def start(self, tickers):
        self._tickers = []
        for t in tickers:
            await self.add_ticker(t)

    async def stop(self):
        pass

    async def add_ticker(self, ticker):
        if ticker not in self.prices:
            raise UnknownTicker(ticker)
        if not self.refresh_on_add:
            raise QuoteUnavailable(ticker)
        if ticker not in self._tickers:
            self._tickers.append(ticker)
        self.cache.update(
            ticker, self.prices[ticker], prev_close=self.prices[ticker], source=self.kind
        )

    async def remove_ticker(self, ticker):
        if ticker in self._tickers:
            self._tickers.remove(ticker)
        self.cache.remove(ticker)

    def get_tickers(self):
        return list(self._tickers)

    async def get_quotes(self, tickers):
        return {t: self.cache.get(t) for t in tickers if self.cache.get(t)}

    async def get_eod_closes(self, tickers, on=None):
        return {t: self.prices[t] for t in tickers if t in self.prices}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)


@pytest.fixture
def cache():
    return PriceCache()


@pytest.fixture
def source(cache):
    return FakeSource(cache)


@pytest.fixture
def client(tmp_path, cache, source):
    app = create_app(
        db_path=tmp_path / "test.db", source=source, cache=cache, snapshot_interval=3600
    )
    with TestClient(app) as c:
        c.app_state = app.state.finally_state
        yield c


def buy(client, ticker="AAPL", quantity=10, side="buy"):
    return client.post(
        "/api/portfolio/trade", json={"ticker": ticker, "quantity": quantity, "side": side}
    )
