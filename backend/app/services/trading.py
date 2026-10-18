"""Trade execution: the single path for manual and chat trades."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..db import transaction
from ..errors import ApiError
from ..market import Quote, QuoteUnavailable, UnknownTicker
from ..schema import insert_snapshot, new_id, now_iso
from ..state import AppState
from .portfolio import total_value, trim_snapshots
from .watchlist import parse_ticker, reconcile_tracked

MIN_QUANTITY = Decimal("0.01")
QTY_STEP = Decimal("0.0001")
DUST = Decimal("0.0001")
CENT = Decimal("0.01")


def parse_quantity(raw) -> Decimal:
    if isinstance(raw, bool) or raw is None:
        raise ApiError(400, "invalid_quantity", "Quantity must be a number")
    try:
        qty = Decimal(str(raw))
    except InvalidOperation:
        raise ApiError(400, "invalid_quantity", "Quantity must be a number") from None
    if not qty.is_finite() or qty <= 0:
        raise ApiError(400, "invalid_quantity", "Quantity must be a positive number")
    if qty != qty.quantize(QTY_STEP):
        raise ApiError(400, "invalid_quantity", "Quantity supports at most 4 decimal places")
    return qty


async def _fill_quote(state: AppState, ticker: str) -> Quote:
    """A fresh quote for the ticker, fetching one first if it is not tracked yet."""
    limit = state.source.max_quote_age()
    quote = state.cache.get_fresh(ticker, limit)
    if quote is None:
        try:
            await state.source.add_ticker(ticker)
        except UnknownTicker as exc:
            raise ApiError(400, "unknown_ticker", str(exc)) from exc
        except QuoteUnavailable as exc:
            raise ApiError(503, "quote_unavailable", str(exc)) from exc
        quote = state.cache.get_fresh(ticker, limit)
    if quote is None:
        raise ApiError(503, "quote_unavailable", f"No current price for {ticker}")
    return quote


async def execute_trade(state: AppState, raw_ticker: str, raw_quantity, side: str) -> dict:
    """Market order at the current price. Caller must hold `state.lock`."""
    ticker = parse_ticker(raw_ticker)
    if side not in ("buy", "sell"):
        raise ApiError(400, "invalid_side", "Side must be 'buy' or 'sell'")
    qty = parse_quantity(raw_quantity)
    try:
        quote = await _fill_quote(state, ticker)
        return _apply_trade(state, ticker, side, qty, quote)
    finally:
        await reconcile_tracked(state)


def _apply_trade(state: AppState, ticker: str, side: str, qty: Decimal, quote: Quote) -> dict:
    price = Decimal(str(quote.price))
    amount = (price * qty).quantize(CENT, rounding=ROUND_HALF_UP)
    now = now_iso()
    with transaction(state.db_path) as conn:
        cash = Decimal(
            str(conn.execute("SELECT cash_balance FROM account WHERE id = 1").fetchone()[0])
        )
        pos = conn.execute("SELECT * FROM positions WHERE ticker = ?", (ticker,)).fetchone()
        held = Decimal(str(pos["quantity"])) if pos else Decimal(0)

        if side == "buy":
            if qty < MIN_QUANTITY:
                raise ApiError(400, "invalid_quantity", f"Minimum order is {MIN_QUANTITY} shares")
            if amount > cash:
                raise ApiError(
                    400,
                    "insufficient_cash",
                    f"Need ${amount} but only ${cash} cash available",
                )
            new_cash = cash - amount
            new_qty = held + qty
            old_cost = held * Decimal(str(pos["avg_cost"])) if pos else Decimal(0)
            avg = ((old_cost + qty * price) / new_qty).quantize(QTY_STEP, rounding=ROUND_HALF_UP)
        else:
            if qty > held:
                raise ApiError(
                    400,
                    "insufficient_shares",
                    f"You hold {held} shares of {ticker}, cannot sell {qty}",
                )
            if qty < MIN_QUANTITY and qty != held:
                raise ApiError(400, "invalid_quantity", f"Minimum order is {MIN_QUANTITY} shares")
            new_cash = cash + amount
            new_qty = held - qty
            avg = Decimal(str(pos["avg_cost"]))

        conn.execute("UPDATE account SET cash_balance = ? WHERE id = 1", (float(new_cash),))
        if new_qty < DUST:
            conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
            position = None
        elif pos:
            conn.execute(
                "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ? WHERE ticker = ?",
                (float(new_qty), float(avg), now, ticker),
            )
            position = {"ticker": ticker, "quantity": float(new_qty), "avg_cost": float(avg)}
        else:
            conn.execute(
                "INSERT INTO positions (id, ticker, quantity, avg_cost, updated_at) VALUES (?, ?, ?, ?, ?)",
                (new_id(), ticker, float(new_qty), float(avg), now),
            )
            position = {"ticker": ticker, "quantity": float(new_qty), "avg_cost": float(avg)}

        trade_id = new_id()
        conn.execute(
            "INSERT INTO trades (id, ticker, side, quantity, price, executed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (trade_id, ticker, side, float(qty), float(price), now),
        )
        value, complete = total_value(conn, state)
        if complete:
            insert_snapshot(conn, value)
            trim_snapshots(conn)

    return {
        "trade": {
            "id": trade_id,
            "ticker": ticker,
            "side": side,
            "quantity": float(qty),
            "price": float(price),
            "total": float(amount),
            "executed_at": now,
        },
        "cash_balance": float(new_cash),
        "position": position,
    }
