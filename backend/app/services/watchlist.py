"""Watchlist management and tracked-ticker reconciliation."""

from __future__ import annotations

import logging

from ..db import read, transaction
from ..errors import ApiError
from ..market import QuoteUnavailable, UnknownTicker, normalize_ticker
from ..schema import new_id, now_iso
from ..state import AppState

logger = logging.getLogger(__name__)


def parse_ticker(raw: str) -> str:
    try:
        return normalize_ticker(raw)
    except UnknownTicker as exc:
        raise ApiError(400, "invalid_ticker", str(exc)) from exc


def watchlist_tickers(state: AppState) -> list[str]:
    with read(state.db_path) as conn:
        return [
            r["ticker"]
            for r in conn.execute("SELECT ticker FROM watchlist ORDER BY added_at, rowid")
        ]


def tracked_set(state: AppState) -> set[str]:
    """Tickers that must stay priced: watchlist union open positions."""
    with read(state.db_path) as conn:
        rows = conn.execute("SELECT ticker FROM watchlist UNION SELECT ticker FROM positions")
        return {r["ticker"] for r in rows}


async def reconcile_tracked(state: AppState) -> None:
    """Make the market source track exactly watchlist ∪ positions."""
    wanted = tracked_set(state)
    current = set(state.source.get_tickers())
    for ticker in current - wanted:
        await state.source.remove_ticker(ticker)
    for ticker in wanted - current:
        try:
            await state.source.add_ticker(ticker)
        except (UnknownTicker, QuoteUnavailable) as exc:
            logger.warning("Cannot track %s: %s", ticker, exc)


def list_watchlist(state: AppState) -> list[dict]:
    with read(state.db_path) as conn:
        rows = conn.execute(
            "SELECT ticker, added_at FROM watchlist ORDER BY added_at, rowid"
        ).fetchall()
    out = []
    for row in rows:
        quote = state.cache.get(row["ticker"])
        out.append(
            {
                "ticker": row["ticker"],
                "added_at": row["added_at"],
                "quote": quote.to_dict() if quote else None,
            }
        )
    return out


async def add_ticker(state: AppState, raw: str) -> dict:
    """Add to the watchlist (idempotent). Rejects tickers that cannot be priced."""
    ticker = parse_ticker(raw)
    try:
        await state.source.add_ticker(ticker)
    except UnknownTicker as exc:
        raise ApiError(400, "unknown_ticker", str(exc)) from exc
    except QuoteUnavailable as exc:
        raise ApiError(503, "quote_unavailable", str(exc)) from exc
    with transaction(state.db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (id, ticker, added_at) VALUES (?, ?, ?)",
            (new_id(), ticker, now_iso()),
        )
    quote = state.cache.get(ticker)
    return {"ticker": ticker, "quote": quote.to_dict() if quote else None}


async def remove_ticker(state: AppState, raw: str) -> bool:
    """Remove from the watchlist. A held ticker stays priced. Returns False if absent."""
    ticker = parse_ticker(raw)
    with transaction(state.db_path) as conn:
        removed = conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,)).rowcount > 0
    if removed:
        await reconcile_tracked(state)
    return removed
