"""Portfolio valuation, snapshots and history."""

from __future__ import annotations

import sqlite3

from ..config import SNAPSHOT_RETENTION
from ..db import read, transaction
from ..schema import insert_snapshot
from ..state import AppState


def _is_stale(state: AppState, quote) -> bool:
    if quote is None:
        return True
    limit = state.source.max_quote_age()
    return limit is not None and quote.age() > limit


def position_view(state: AppState, row: sqlite3.Row) -> dict:
    quote = state.cache.get(row["ticker"])
    qty, avg = row["quantity"], row["avg_cost"]
    cost = qty * avg
    if quote is None:
        value, pnl, pnl_pct, price, day_pct = round(cost, 2), None, None, None, None
    else:
        price = quote.price
        value = round(qty * price, 2)
        pnl = round(value - cost, 2)
        pnl_pct = round((price - avg) / avg * 100, 2) if avg else None
        day_pct = quote.day_change_pct
    return {
        "ticker": row["ticker"],
        "quantity": qty,
        "avg_cost": avg,
        "price": price,
        "market_value": value,  # falls back to cost basis while no price is available
        "unrealized_pnl": pnl,
        "unrealized_pnl_pct": pnl_pct,
        "day_change_pct": day_pct,
        "stale": _is_stale(state, quote),
    }


def get_portfolio(state: AppState) -> dict:
    with read(state.db_path) as conn:
        cash = conn.execute("SELECT cash_balance FROM account WHERE id = 1").fetchone()[0]
        rows = conn.execute("SELECT * FROM positions ORDER BY ticker").fetchall()
    positions = [position_view(state, r) for r in rows]
    positions_value = round(sum(p["market_value"] for p in positions), 2)
    cost = sum(p["quantity"] * p["avg_cost"] for p in positions)
    pnl = round(sum(p["unrealized_pnl"] or 0.0 for p in positions), 2)
    return {
        "cash_balance": cash,
        "positions": positions,
        "positions_value": positions_value,
        "total_value": round(cash + positions_value, 2),
        "unrealized_pnl": pnl,
        "unrealized_pnl_pct": round(pnl / cost * 100, 2) if cost else 0.0,
        "prices_complete": all(not p["stale"] for p in positions),
        "market": {"source": state.source.kind.value, "status": state.source.status.value},
    }


def total_value(conn: sqlite3.Connection, state: AppState) -> tuple[float, bool]:
    """(cash + holdings at fresh prices, complete). Incomplete when any holding lacks a fresh price."""
    cash = conn.execute("SELECT cash_balance FROM account WHERE id = 1").fetchone()[0]
    limit = state.source.max_quote_age()
    value, complete = cash, True
    for row in conn.execute("SELECT ticker, quantity, avg_cost FROM positions"):
        quote = state.cache.get_fresh(row["ticker"], limit)
        if quote is None:
            complete = False
            value += row["quantity"] * row["avg_cost"]
        else:
            value += row["quantity"] * quote.price
    return round(value, 2), complete


def trim_snapshots(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM portfolio_snapshots WHERE rowid IN "
        "(SELECT rowid FROM portfolio_snapshots ORDER BY rowid DESC LIMIT -1 OFFSET ?)",
        (SNAPSHOT_RETENTION,),
    )


def record_snapshot(state: AppState) -> bool:
    """Persist the current total value. Skipped (False) when any price is missing or stale."""
    with transaction(state.db_path) as conn:
        value, complete = total_value(conn, state)
        if not complete:
            return False
        insert_snapshot(conn, value)
        trim_snapshots(conn)
        return True


def history(state: AppState, limit: int = 2000) -> list[dict]:
    with read(state.db_path) as conn:
        rows = conn.execute(
            "SELECT total_value, recorded_at FROM portfolio_snapshots ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"total_value": r["total_value"], "recorded_at": r["recorded_at"]} for r in reversed(rows)
    ]
