"""REST routes (the SSE stream is provided by app.market.create_stream_router)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from . import chat as chat_service
from .db import read
from .errors import ApiError
from .services import lifecycle, portfolio, trading, watchlist
from .state import AppState

router = APIRouter(prefix="/api")


def _state(request: Request) -> AppState:
    return request.app.state.finally_state


class TradeRequest(BaseModel):
    ticker: str
    quantity: float | str
    side: Literal["buy", "sell"]


class WatchlistAdd(BaseModel):
    ticker: str


class ChatRequest(BaseModel):
    message: str


@router.get("/health")
async def health(request: Request) -> dict:
    state = _state(request)
    with read(state.db_path) as conn:
        conn.execute("SELECT 1 FROM account WHERE id = 1").fetchone()
    return {
        "status": "ok",
        "market": {
            "source": state.source.kind.value,
            "status": state.source.status.value,
            "tracked": len(state.source.get_tickers()),
        },
    }


@router.get("/portfolio")
async def get_portfolio(request: Request) -> dict:
    return portfolio.get_portfolio(_state(request))


@router.post("/portfolio/trade")
async def post_trade(body: TradeRequest, request: Request) -> dict:
    state = _state(request)
    async with state.lock:
        return await trading.execute_trade(state, body.ticker, body.quantity, body.side)


@router.get("/portfolio/history")
async def get_history(request: Request, limit: int = Query(2000, ge=1, le=20000)) -> dict:
    return {"snapshots": portfolio.history(_state(request), limit)}


@router.post("/reset")
async def post_reset(request: Request) -> dict:
    state = _state(request)
    async with state.lock:
        await lifecycle.reset_account(state)
    return {"status": "reset"}


@router.get("/watchlist")
async def get_watchlist(request: Request) -> dict:
    return {"items": watchlist.list_watchlist(_state(request))}


@router.post("/watchlist", status_code=201)
async def post_watchlist(body: WatchlistAdd, request: Request) -> dict:
    state = _state(request)
    async with state.lock:
        return await watchlist.add_ticker(state, body.ticker)


@router.delete("/watchlist/{ticker}")
async def delete_watchlist(ticker: str, request: Request) -> dict:
    state = _state(request)
    async with state.lock:
        if not await watchlist.remove_ticker(state, ticker):
            raise ApiError(404, "not_in_watchlist", f"{ticker.upper()} is not on the watchlist")
    return {"removed": ticker.strip().upper()}


@router.post("/chat")
async def post_chat(body: ChatRequest, request: Request) -> dict:
    return await chat_service.handle_chat(_state(request), body.message)


@router.get("/chat/history")
async def get_chat_history(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict:
    return {"messages": chat_service.load_history(_state(request), limit)}
