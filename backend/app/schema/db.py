"""SQLite schema, connection helpers and lazy initialization/seeding."""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]
STARTING_CASH = 10000.0
SNAPSHOT_RETENTION = 20_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash_balance REAL NOT NULL DEFAULT 10000.0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    quantity REAL NOT NULL,
    avg_cost REAL NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    executed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id TEXT PRIMARY KEY,
    total_value REAL NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    actions TEXT,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def default_db_path() -> Path:
    """DB location: $FINALLY_DB_PATH, else <project root>/db/finally.db."""
    env = os.environ.get("FINALLY_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "db" / "finally.db"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection in autocommit mode (transactions are explicit)."""
    conn = sqlite3.connect(str(db_path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Write transaction that takes the write lock up front (BEGIN IMMEDIATE)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def record_snapshot(conn: sqlite3.Connection, total_value: float) -> None:
    """Insert a portfolio snapshot and prune to the retention limit."""
    conn.execute(
        "INSERT INTO portfolio_snapshots (id, total_value, recorded_at) VALUES (?, ?, ?)",
        (new_id(), round(total_value, 2), now_iso()),
    )
    conn.execute(
        "DELETE FROM portfolio_snapshots WHERE rowid NOT IN "
        "(SELECT rowid FROM portfolio_snapshots ORDER BY recorded_at DESC, rowid DESC LIMIT ?)",
        (SNAPSHOT_RETENTION,),
    )


def seed_defaults(conn: sqlite3.Connection) -> None:
    """Insert the default account, snapshot and watchlist. Caller owns the transaction."""
    ts = now_iso()
    conn.execute(
        "INSERT INTO account (id, cash_balance, created_at) VALUES (1, ?, ?)",
        (STARTING_CASH, ts),
    )
    for ticker in DEFAULT_TICKERS:
        conn.execute(
            "INSERT INTO watchlist (id, ticker, added_at) VALUES (?, ?, ?)",
            (new_id(), ticker, ts),
        )
    record_snapshot(conn, STARTING_CASH)


def init_db(db_path: str | Path) -> None:
    """Create tables and seed defaults if the database is new/empty. Idempotent."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        with transaction(conn):
            if conn.execute("SELECT 1 FROM account WHERE id = 1").fetchone() is None:
                seed_defaults(conn)
    finally:
        conn.close()


def reset_db(conn: sqlite3.Connection) -> None:
    """Reset to a freshly seeded state in a single transaction."""
    with transaction(conn):
        for table in ("positions", "trades", "portfolio_snapshots", "chat_messages",
                      "watchlist", "account"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
        seed_defaults(conn)
