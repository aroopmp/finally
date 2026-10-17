"""Portfolio, watchlist and trade logic. Pure functions over a sqlite connection."""

from __future__ import annotations

import math
import re
import sqlite3

from app.market import PriceCache
from app.schema import now_iso, record_snapshot, transaction
from app.schema.db import new_id

MIN_QUANTITY = 0.01
POSITION_EPSILON = 0.0001
_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


class TradeError(Exception):
    """A request failed validation. `status` is the suggested HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_ticker(raw: object) -> str:
    """Trim + uppercase; must be 1-5 letters A-Z."""
    ticker = str(raw or "").strip().upper()
    if not _TICKER_RE.match(ticker):
        raise TradeError(f"Invalid ticker: {raw!r}")
    return ticker


def validate_quantity(raw: object) -> float:
    """Quantity >= 0.01 with at most 4 decimal places."""
    try:
        qty = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TradeError("Invalid quantity") from None
    if not math.isfinite(qty) or qty < MIN_QUANTITY - 1e-12:
        raise TradeError(f"Quantity must be at least {MIN_QUANTITY}")
    if abs(qty - round(qty, 4)) > 1e-9:
        raise TradeError("Quantity supports at most 4 decimal places")
    return round(qty, 4)


def tracked_tickers(conn: sqlite3.Connection) -> set[str]:
    """Tickers that must stay priced: watchlist union open positions."""
    rows = conn.execute(
        "SELECT ticker FROM watchlist UNION SELECT ticker FROM positions"
    ).fetchall()
    return {r["ticker"] for r in rows}


def _price_for(cache: PriceCache, ticker: str, fallback: float) -> float:
    price = cache.get_price(ticker)
    return price if price is not None and price > 0 else fallback


def compute_total_value(conn: sqlite3.Connection, cache: PriceCache) -> float:
    """Cash + market value. Unpriced holdings fall back to cost, never zero."""
    cash = conn.execute("SELECT cash_balance FROM account WHERE id = 1").fetchone()[0]
    total = cash
    for row in conn.execute("SELECT ticker, quantity, avg_cost FROM positions"):
        total += row["quantity"] * _price_for(cache, row["ticker"], row["avg_cost"])
    return round(total, 2)


def get_portfolio(conn: sqlite3.Connection, cache: PriceCache) -> dict:
    cash = conn.execute("SELECT cash_balance FROM account WHERE id = 1").fetchone()[0]
    positions = []
    market_total = 0.0
    pnl_total = 0.0
    for row in conn.execute("SELECT * FROM positions ORDER BY ticker"):
        price = _price_for(cache, row["ticker"], row["avg_cost"])
        value = row["quantity"] * price
        cost = row["quantity"] * row["avg_cost"]
        pnl = value - cost
        market_total += value
        pnl_total += pnl
        positions.append(
            {
                "ticker": row["ticker"],
                "quantity": row["quantity"],
                "avg_cost": round(row["avg_cost"], 4),
                "current_price": round(price, 2),
                "market_value": round(value, 2),
                "unrealized_pnl": round(pnl, 2),
                "pnl_percent": round(pnl / cost * 100, 2) if cost else 0.0,
            }
        )
    return {
        "cash_balance": round(cash, 2),
        "positions": positions,
        "total_value": round(cash + market_total, 2),
        "unrealized_pnl": round(pnl_total, 2),
    }


def get_history(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT total_value, recorded_at FROM portfolio_snapshots "
        "ORDER BY recorded_at ASC, rowid ASC"
    ).fetchall()
    return [{"total_value": r["total_value"], "recorded_at": r["recorded_at"]} for r in rows]


def execute_trade(
    conn: sqlite3.Connection, cache: PriceCache, ticker: str, side: str, quantity: float
) -> dict:
    """Execute a market order atomically. Shared by the REST route and (later) chat.

    Validates, fills at the cached price, updates cash/position, appends to the
    trade log and records a snapshot, all in one write transaction.
    """
    ticker = normalize_ticker(ticker)
    side = str(side or "").strip().lower()
    if side not in ("buy", "sell"):
        raise TradeError("Side must be 'buy' or 'sell'")
    qty = validate_quantity(quantity)
    price = cache.get_price(ticker)
    if price is None or not math.isfinite(price) or price <= 0:
        raise TradeError(f"No price available for {ticker}")

    ts = now_iso()
    with transaction(conn):
        cash = conn.execute("SELECT cash_balance FROM account WHERE id = 1").fetchone()[0]
        pos = conn.execute("SELECT * FROM positions WHERE ticker = ?", (ticker,)).fetchone()
        notional = round(qty * price, 2)

        if side == "buy":
            if notional > cash + 1e-9:
                raise TradeError(
                    f"Insufficient cash: need ${notional:,.2f}, have ${cash:,.2f}"
                )
            cash = round(cash - notional, 2)
            if pos:
                new_qty = round(pos["quantity"] + qty, 4)
                new_avg = (pos["quantity"] * pos["avg_cost"] + qty * price) / new_qty
                conn.execute(
                    "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ? "
                    "WHERE ticker = ?",
                    (new_qty, new_avg, ts, ticker),
                )
            else:
                conn.execute(
                    "INSERT INTO positions (id, ticker, quantity, avg_cost, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (new_id(), ticker, qty, price, ts),
                )
        else:
            held = pos["quantity"] if pos else 0.0
            if qty > held + 1e-9:
                raise TradeError(f"Insufficient shares: trying to sell {qty}, hold {held}")
            cash = round(cash + notional, 2)
            remaining = round(held - qty, 4)
            if remaining < POSITION_EPSILON:
                conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
            else:
                conn.execute(
                    "UPDATE positions SET quantity = ?, updated_at = ? WHERE ticker = ?",
                    (remaining, ts, ticker),
                )

        conn.execute("UPDATE account SET cash_balance = ? WHERE id = 1", (cash,))
        trade_id = new_id()
        conn.execute(
            "INSERT INTO trades (id, ticker, side, quantity, price, executed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (trade_id, ticker, side, qty, price, ts),
        )
        record_snapshot(conn, compute_total_value(conn, cache))

    return {
        "id": trade_id,
        "ticker": ticker,
        "side": side,
        "quantity": qty,
        "price": price,
        "total": notional,
        "executed_at": ts,
        "cash_balance": cash,
    }


def get_watchlist(conn: sqlite3.Connection, cache: PriceCache) -> list[dict]:
    items = []
    for row in conn.execute("SELECT ticker FROM watchlist ORDER BY added_at, rowid"):
        update = cache.get(row["ticker"])
        items.append(
            {
                "ticker": row["ticker"],
                "price": update.price if update else None,
                "previous_price": update.previous_price if update else None,
                "change_percent": round(update.change_percent, 2) if update else None,
                "direction": update.direction if update else None,
            }
        )
    return items
