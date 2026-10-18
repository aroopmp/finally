"""Schema, seeding, transaction, retention and reset behavior."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app import config
from app.db import init_db, read, transaction
from app.schema import insert_snapshot
from app.services.portfolio import trim_snapshots
from tests.conftest import buy


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sub" / "t.db"
    init_db(path)
    return path


def test_init_creates_parent_dir_and_seeds(db):
    with read(db) as conn:
        acct = conn.execute("SELECT * FROM account").fetchall()
        assert len(acct) == 1 and acct[0]["id"] == 1 and acct[0]["cash_balance"] == 10000.0
        tickers = [r[0] for r in conn.execute("SELECT ticker FROM watchlist ORDER BY ticker")]
        assert tickers == sorted(config.DEFAULT_WATCHLIST)
        snaps = conn.execute("SELECT total_value FROM portfolio_snapshots").fetchall()
        assert [s[0] for s in snaps] == [10000.0]


def test_init_is_idempotent_and_preserves_data(db):
    with transaction(db) as conn:
        conn.execute("UPDATE account SET cash_balance = 123.45")
        conn.execute("DELETE FROM watchlist WHERE ticker = 'AAPL'")
    init_db(db)
    with read(db) as conn:
        assert conn.execute("SELECT cash_balance FROM account").fetchone()[0] == 123.45
        assert conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 9
        assert conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0] == 1


def test_init_recreates_missing_tables(db):
    with transaction(db) as conn:
        conn.execute("DROP TABLE chat_messages")
    init_db(db)
    with read(db) as conn:
        conn.execute("SELECT * FROM chat_messages").fetchall()


def test_account_single_row_constraint(db):
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(db) as conn:
            conn.execute("INSERT INTO account (id, cash_balance, created_at) VALUES (2, 1, 'x')")


def test_unique_ticker_constraints(db):
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(db) as conn:
            conn.execute("INSERT INTO watchlist (id, ticker, added_at) VALUES ('a', 'AAPL', 'x')")
    with transaction(db) as conn:
        conn.execute("INSERT INTO positions VALUES ('p1', 'AAPL', 1, 1, 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(db) as conn:
            conn.execute("INSERT INTO positions VALUES ('p2', 'AAPL', 1, 1, 'x')")


def test_side_and_role_checks(db):
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(db) as conn:
            conn.execute("INSERT INTO trades VALUES ('t', 'AAPL', 'hold', 1, 1, 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(db) as conn:
            conn.execute("INSERT INTO chat_messages VALUES ('c', 'system', 'hi', NULL, 'x')")


def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        with transaction(db) as conn:
            conn.execute("UPDATE account SET cash_balance = 1")
            conn.execute("INSERT INTO positions VALUES ('p1', 'AAPL', 1, 1, 'x')")
            raise RuntimeError("boom")
    with read(db) as conn:
        assert conn.execute("SELECT cash_balance FROM account").fetchone()[0] == 10000.0
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


def test_transaction_serializes_read_modify_write(db):
    """BEGIN IMMEDIATE: concurrent increments never lose an update."""

    def bump():
        for _ in range(20):
            with transaction(db) as conn:
                cash = conn.execute("SELECT cash_balance FROM account").fetchone()[0]
                conn.execute("UPDATE account SET cash_balance = ?", (cash + 1,))

    threads = [threading.Thread(target=bump) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with read(db) as conn:
        assert conn.execute("SELECT cash_balance FROM account").fetchone()[0] == 10080.0


def test_snapshot_retention_keeps_most_recent(db, monkeypatch):
    monkeypatch.setattr("app.services.portfolio.SNAPSHOT_RETENTION", 50)
    with transaction(db) as conn:
        for i in range(80):
            insert_snapshot(conn, float(i))
            trim_snapshots(conn)
    with read(db) as conn:
        vals = [r[0] for r in conn.execute("SELECT total_value FROM portfolio_snapshots ORDER BY rowid")]
    assert len(vals) == 50
    assert vals[-1] == 79.0 and vals[0] == 30.0


def test_retention_constant():
    assert config.SNAPSHOT_RETENTION == 20_000


def test_reset_restores_defaults(client):
    assert buy(client, "AAPL", 5).status_code == 200
    client.post("/api/chat", json={"message": "hello"})
    client.delete("/api/watchlist/MSFT")
    assert client.post("/api/reset").status_code == 200
    with read(client.app_state.db_path) as conn:
        assert conn.execute("SELECT cash_balance FROM account").fetchone()[0] == 10000.0
        for table in ("positions", "trades", "chat_messages"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 10
        snaps = conn.execute("SELECT total_value FROM portfolio_snapshots").fetchall()
        assert [s[0] for s in snaps] == [10000.0]


def test_money_rules_via_trades(client):
    # fractional shares at 4dp accepted; cash rounded to cents
    r = buy(client, "AAPL", 0.3333)
    assert r.status_code == 200
    cash = r.json()["cash_balance"]
    assert cash == round(cash, 2) == 9966.67
    # more than 4 decimals, below minimum, zero, negative rejected
    for q in (0.12345, 0.001, 0, -1):
        assert buy(client, "AAPL", q).status_code == 400
    # selling everything deletes the position
    r = buy(client, "AAPL", 0.3333, side="sell")
    assert r.status_code == 200 and r.json()["position"] is None
    with read(client.app_state.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


def test_sell_leaving_exactly_min_dust_keeps_position(client):
    assert buy(client, "AAPL", 1.0001).status_code == 200
    r = buy(client, "AAPL", 1, side="sell")  # leaves 0.0001, not below the threshold
    assert r.status_code == 200 and r.json()["position"]["quantity"] == pytest.approx(0.0001)
