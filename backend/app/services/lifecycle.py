"""Account reset."""

from __future__ import annotations

from ..config import STARTING_CASH
from ..db import transaction
from ..schema import insert_default_watchlist, insert_snapshot
from ..state import AppState
from .watchlist import tracked_set


async def reset_account(state: AppState) -> None:
    """Restore the initial account. Caller must hold `state.lock`.

    Bumps the epoch so chat responses already in flight are discarded, then restarts
    the market source so prices return to a known state.
    """
    state.epoch += 1
    with transaction(state.db_path) as conn:
        for table in ("positions", "trades", "portfolio_snapshots", "chat_messages", "watchlist"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("UPDATE account SET cash_balance = ? WHERE id = 1", (STARTING_CASH,))
        insert_default_watchlist(conn)
        insert_snapshot(conn, STARTING_CASH)
    await state.source.stop()
    state.cache.clear()
    await state.source.start(sorted(tracked_set(state)))
