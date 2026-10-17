"""FastAPI application entry point.

Run with: uvicorn app.main:app
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI

from app.api import create_api_router
from app.market import MarketDataSource, PriceCache, create_market_data_source, create_stream_router
from app.portfolio import compute_total_value, tracked_tickers
from app.schema import connect, default_db_path, init_db, record_snapshot

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_SECONDS = 30


async def _snapshot_loop(db_path: Path | str, cache: PriceCache, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            conn = connect(db_path)
            try:
                record_snapshot(conn, compute_total_value(conn, cache))
            finally:
                conn.close()
        except Exception:
            logger.exception("Snapshot failed")


def create_app(
    db_path: Path | str | None = None,
    price_cache: PriceCache | None = None,
    source: MarketDataSource | None = None,
    snapshot_interval: float = SNAPSHOT_INTERVAL_SECONDS,
) -> FastAPI:
    db_path = Path(db_path) if db_path else default_db_path()
    cache = price_cache or PriceCache()
    data_source = source or create_market_data_source(cache)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Order: init/seed DB -> load tracked set -> start market data -> snapshots
        init_db(db_path)
        conn = connect(db_path)
        try:
            tickers = sorted(tracked_tickers(conn))
        finally:
            conn.close()
        await data_source.start(tickers)
        task = asyncio.create_task(_snapshot_loop(db_path, cache, snapshot_interval))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await data_source.stop()

    app = FastAPI(title="FinAlly", lifespan=lifespan)
    app.include_router(create_stream_router(cache))
    app.include_router(create_api_router(db_path, cache, data_source))
    return app


app = create_app()
