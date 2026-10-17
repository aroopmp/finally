"""REST routes: health, portfolio, trade, history, reset, watchlist."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.market import MarketDataSource, PriceCache
from app.market.massive_client import MassiveDataSource
from app.market.seed_prices import SEED_PRICES
from app.portfolio import (
    TradeError,
    execute_trade,
    get_history,
    get_portfolio,
    get_watchlist,
    normalize_ticker,
    tracked_tickers,
)
from app.schema import DEFAULT_TICKERS, connect, now_iso, reset_db
from app.schema.db import new_id


class TradeRequest(BaseModel):
    ticker: str
    quantity: float
    side: str


class WatchlistRequest(BaseModel):
    ticker: str


class MarketAccess:
    """Keeps the market data source in sync with the tracked-ticker set."""

    def __init__(self, cache: PriceCache, source: MarketDataSource) -> None:
        self.cache = cache
        self.source = source

    async def ensure_priced(self, ticker: str) -> None:
        """Make sure `ticker` has a price, or raise TradeError (400) if unsupported."""
        if self.cache.get_price(ticker) is not None:
            return
        if not isinstance(self.source, MassiveDataSource) and ticker not in SEED_PRICES:
            raise TradeError(f"Unknown ticker: {ticker}")
        await self.source.add_ticker(ticker)
        if self.cache.get_price(ticker) is None:
            await self.source.remove_ticker(ticker)
            raise TradeError(f"No price available for {ticker}")

    async def reconcile(self, tracked: set[str]) -> None:
        """Drop untracked tickers from the source and add missing tracked ones."""
        current = set(self.source.get_tickers())
        for ticker in current - tracked:
            await self.source.remove_ticker(ticker)
        for ticker in tracked - current:
            await self.source.add_ticker(ticker)


def _http(err: TradeError) -> HTTPException:
    return HTTPException(status_code=err.status, detail=err.message)


def create_api_router(db_path: Path | str, cache: PriceCache, source: MarketDataSource) -> APIRouter:
    router = APIRouter(prefix="/api")
    market = MarketAccess(cache, source)

    @router.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @router.get("/portfolio")
    async def portfolio() -> dict:
        conn = connect(db_path)
        try:
            return get_portfolio(conn, cache)
        finally:
            conn.close()

    @router.post("/portfolio/trade")
    async def trade(req: TradeRequest) -> dict:
        conn = connect(db_path)
        try:
            ticker = normalize_ticker(req.ticker)
            await market.ensure_priced(ticker)
            result = execute_trade(conn, cache, ticker, req.side, req.quantity)
            await market.reconcile(tracked_tickers(conn))
            return result
        except TradeError as err:
            raise _http(err) from None
        finally:
            conn.close()

    @router.get("/portfolio/history")
    async def history() -> list[dict]:
        conn = connect(db_path)
        try:
            return get_history(conn)
        finally:
            conn.close()

    @router.post("/reset")
    async def reset() -> dict:
        conn = connect(db_path)
        try:
            reset_db(conn)
            await market.reconcile(set(DEFAULT_TICKERS))
            return {"status": "reset"}
        finally:
            conn.close()

    @router.get("/watchlist")
    async def watchlist() -> list[dict]:
        conn = connect(db_path)
        try:
            return get_watchlist(conn, cache)
        finally:
            conn.close()

    @router.post("/watchlist")
    async def add_watchlist(req: WatchlistRequest) -> dict:
        conn = connect(db_path)
        try:
            ticker = normalize_ticker(req.ticker)
            await market.ensure_priced(ticker)
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (id, ticker, added_at) VALUES (?, ?, ?)",
                (new_id(), ticker, now_iso()),
            )
            return next(i for i in get_watchlist(conn, cache) if i["ticker"] == ticker)
        except TradeError as err:
            raise _http(err) from None
        finally:
            conn.close()

    @router.delete("/watchlist/{ticker}")
    async def remove_watchlist(ticker: str) -> dict:
        conn = connect(db_path)
        try:
            ticker = normalize_ticker(ticker)
            cur = conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"{ticker} is not on the watchlist")
            await market.reconcile(tracked_tickers(conn))
            return {"status": "removed", "ticker": ticker}
        except TradeError as err:
            raise _http(err) from None
        finally:
            conn.close()

    return router
