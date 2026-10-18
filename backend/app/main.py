"""FastAPI application: REST + SSE + static frontend, single port."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import init_db
from .errors import ApiError
from .market import MarketDataSource, PriceCache, create_market_data_source, create_stream_router
from .routes import router
from .services import portfolio
from .services.watchlist import tracked_set
from .state import AppState

logger = logging.getLogger(__name__)


async def _snapshot_loop(state: AppState, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            async with state.lock:
                portfolio.record_snapshot(state)
        except Exception:  # noqa: BLE001
            logger.exception("Snapshot failed")


def create_app(
    db_path: Path | None = None,
    source: MarketDataSource | None = None,
    cache: PriceCache | None = None,
    snapshot_interval: float = config.SNAPSHOT_INTERVAL_SECONDS,
    static_dir: Path | None = None,
) -> FastAPI:
    """Build the app. Parameters exist for tests; production uses environment defaults."""
    # `is None` checks: an empty PriceCache is falsy (it defines __len__).
    cache = PriceCache() if cache is None else cache
    source = create_market_data_source(cache) if source is None else source
    path = config.db_path() if db_path is None else db_path

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(path)
        state = AppState(db_path=path, cache=cache, source=source)
        app.state.finally_state = state
        await source.start(sorted(tracked_set(state)))
        task = asyncio.create_task(_snapshot_loop(state, snapshot_interval), name="snapshots")
        try:
            yield
        finally:
            task.cancel()
            await source.stop()

    app = FastAPI(title="FinAlly", lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_request",
                    "message": f"{loc}: {first.get('msg', 'invalid')}",
                }
            },
            status_code=400,
        )

    app.include_router(router)
    app.include_router(create_stream_router(cache))

    static = static_dir or config.static_dir()
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app


def create_default_app() -> FastAPI:
    """uvicorn factory: loads .env for local runs, then builds the app."""
    logging.basicConfig(level=logging.INFO)
    config.load_dotenv()
    return create_app()
