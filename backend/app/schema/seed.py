"""Schema creation and default seed data (idempotent)."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..config import DEFAULT_WATCHLIST, STARTING_CASH

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def create_schema(conn: sqlite3.Connection) -> None:
    # Statement by statement: executescript() would COMMIT the caller's open transaction.
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            conn.execute(statement)


def seed_defaults(conn: sqlite3.Connection) -> None:
    """Insert the account row, default watchlist and baseline snapshot if the account is missing."""
    if conn.execute("SELECT 1 FROM account WHERE id = 1").fetchone():
        return
    now = now_iso()
    conn.execute(
        "INSERT INTO account (id, cash_balance, created_at) VALUES (1, ?, ?)",
        (STARTING_CASH, now),
    )
    insert_default_watchlist(conn)
    insert_snapshot(conn, STARTING_CASH)


def insert_default_watchlist(conn: sqlite3.Connection) -> None:
    now = now_iso()
    conn.executemany(
        "INSERT OR IGNORE INTO watchlist (id, ticker, added_at) VALUES (?, ?, ?)",
        [(new_id(), t, now) for t in DEFAULT_WATCHLIST],
    )


def insert_snapshot(conn: sqlite3.Connection, total_value: float) -> None:
    conn.execute(
        "INSERT INTO portfolio_snapshots (id, total_value, recorded_at) VALUES (?, ?, ?)",
        (new_id(), round(total_value, 2), now_iso()),
    )
