"""Unit tests for DB init and the trade/portfolio service."""

import pytest

from app.market import PriceCache
from app.portfolio import (
    TradeError,
    execute_trade,
    get_history,
    get_portfolio,
    get_watchlist,
    normalize_ticker,
    tracked_tickers,
    validate_quantity,
)
from app.schema import DEFAULT_TICKERS, connect, init_db, reset_db


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    c = connect(path)
    yield c
    c.close()


@pytest.fixture
def cache():
    c = PriceCache()
    c.update("AAPL", 100.0)
    c.update("TSLA", 200.0)
    return c


def test_init_seeds_defaults(conn):
    assert conn.execute("SELECT cash_balance FROM account").fetchone()[0] == 10000.0
    assert tracked_tickers(conn) == set(DEFAULT_TICKERS)
    assert [h["total_value"] for h in get_history(conn)] == [10000.0]


def test_init_is_idempotent(tmp_path):
    path = tmp_path / "x.db"
    init_db(path)
    init_db(path)
    c = connect(path)
    assert c.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 10
    assert c.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0] == 1


@pytest.mark.parametrize("raw,expected", [(" aapl ", "AAPL"), ("v", "V")])
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "TOOLONG", "AB1", "A-B", None])
def test_normalize_ticker_invalid(raw):
    with pytest.raises(TradeError):
        normalize_ticker(raw)


@pytest.mark.parametrize("raw", [0, 0.001, -1, 1.23456, "abc", float("nan"), float("inf")])
def test_validate_quantity_invalid(raw):
    with pytest.raises(TradeError):
        validate_quantity(raw)


def test_validate_quantity_valid():
    assert validate_quantity(0.01) == 0.01
    assert validate_quantity("1.2345") == 1.2345


def test_buy_creates_position_and_reduces_cash(conn, cache):
    result = execute_trade(conn, cache, "aapl", "buy", 10)
    assert result["price"] == 100.0 and result["cash_balance"] == 9000.0
    p = get_portfolio(conn, cache)
    assert p["cash_balance"] == 9000.0
    assert p["total_value"] == 10000.0
    assert p["positions"][0]["ticker"] == "AAPL"
    assert p["positions"][0]["quantity"] == 10


def test_buy_averages_cost(conn, cache):
    execute_trade(conn, cache, "AAPL", "buy", 10)
    cache.update("AAPL", 120.0)
    execute_trade(conn, cache, "AAPL", "buy", 10)
    pos = get_portfolio(conn, cache)["positions"][0]
    assert pos["quantity"] == 20
    assert pos["avg_cost"] == 110.0
    assert pos["unrealized_pnl"] == 200.0
    assert pos["pnl_percent"] == pytest.approx(9.09, abs=0.01)


def test_buy_insufficient_cash(conn, cache):
    with pytest.raises(TradeError, match="Insufficient cash"):
        execute_trade(conn, cache, "AAPL", "buy", 101)
    assert get_portfolio(conn, cache)["cash_balance"] == 10000.0
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_buy_exact_cash(conn, cache):
    execute_trade(conn, cache, "AAPL", "buy", 100)
    assert get_portfolio(conn, cache)["cash_balance"] == 0.0


def test_sell_partial_and_full(conn, cache):
    execute_trade(conn, cache, "AAPL", "buy", 10)
    cache.update("AAPL", 110.0)
    execute_trade(conn, cache, "AAPL", "sell", 4)
    p = get_portfolio(conn, cache)
    assert p["positions"][0]["quantity"] == 6
    assert p["cash_balance"] == 9440.0
    execute_trade(conn, cache, "AAPL", "sell", 6)
    p = get_portfolio(conn, cache)
    assert p["positions"] == []
    assert p["cash_balance"] == 10100.0  # +$100 realised profit


def test_sell_at_loss(conn, cache):
    execute_trade(conn, cache, "AAPL", "buy", 10)
    cache.update("AAPL", 90.0)
    assert get_portfolio(conn, cache)["unrealized_pnl"] == -100.0
    execute_trade(conn, cache, "AAPL", "sell", 10)
    assert get_portfolio(conn, cache)["cash_balance"] == 9900.0


def test_oversell_and_sell_unowned(conn, cache):
    execute_trade(conn, cache, "AAPL", "buy", 1)
    with pytest.raises(TradeError, match="Insufficient shares"):
        execute_trade(conn, cache, "AAPL", "sell", 2)
    with pytest.raises(TradeError, match="Insufficient shares"):
        execute_trade(conn, cache, "TSLA", "sell", 1)


def test_fractional_shares(conn, cache):
    execute_trade(conn, cache, "AAPL", "buy", 0.5)
    assert get_portfolio(conn, cache)["cash_balance"] == 9950.0
    execute_trade(conn, cache, "AAPL", "sell", 0.5)
    assert get_portfolio(conn, cache)["positions"] == []


def test_cash_rounded_to_cents(conn, cache):
    cache.update("AAPL", 33.333)  # cached as 33.33
    execute_trade(conn, cache, "AAPL", "buy", 0.33)
    cash = get_portfolio(conn, cache)["cash_balance"]
    assert cash == round(cash, 2)


def test_no_price_rejected(conn, cache):
    with pytest.raises(TradeError, match="No price"):
        execute_trade(conn, cache, "NVDA", "buy", 1)


def test_invalid_side(conn, cache):
    with pytest.raises(TradeError):
        execute_trade(conn, cache, "AAPL", "hold", 1)


def test_trade_records_trade_and_snapshot(conn, cache):
    execute_trade(conn, cache, "AAPL", "buy", 1)
    row = conn.execute("SELECT * FROM trades").fetchone()
    assert (row["ticker"], row["side"], row["quantity"], row["price"]) == ("AAPL", "buy", 1, 100.0)
    assert len(get_history(conn)) == 2


def test_failed_trade_rolls_back(conn, cache, monkeypatch):
    import app.portfolio.service as svc

    def boom(*a, **k):
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(svc, "record_snapshot", boom)
    with pytest.raises(RuntimeError):
        execute_trade(conn, cache, "AAPL", "buy", 1)
    assert conn.execute("SELECT cash_balance FROM account").fetchone()[0] == 10000.0
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_unpriced_holding_falls_back_to_cost(conn, cache):
    execute_trade(conn, cache, "AAPL", "buy", 10)
    cache.remove("AAPL")
    assert get_portfolio(conn, cache)["total_value"] == 10000.0


def test_tracked_includes_held_ticker_removed_from_watchlist(conn, cache):
    execute_trade(conn, cache, "AAPL", "buy", 1)
    conn.execute("DELETE FROM watchlist WHERE ticker = 'AAPL'")
    assert "AAPL" in tracked_tickers(conn)


def test_watchlist_prices(conn, cache):
    items = {i["ticker"]: i for i in get_watchlist(conn, cache)}
    assert items["AAPL"]["price"] == 100.0
    assert items["MSFT"]["price"] is None


def test_reset(conn, cache):
    execute_trade(conn, cache, "AAPL", "buy", 5)
    conn.execute("DELETE FROM watchlist WHERE ticker = 'V'")
    reset_db(conn)
    p = get_portfolio(conn, cache)
    assert p["cash_balance"] == 10000.0 and p["positions"] == []
    assert tracked_tickers(conn) == set(DEFAULT_TICKERS)
    assert len(get_history(conn)) == 1
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_snapshot_retention(conn):
    from app.schema import db as dbmod
    from app.schema import record_snapshot

    dbmod.SNAPSHOT_RETENTION = 5
    try:
        for i in range(10):
            record_snapshot(conn, 100.0 + i)
    finally:
        dbmod.SNAPSHOT_RETENTION = 20_000
    history = get_history(conn)
    assert len(history) == 5
    assert history[-1]["total_value"] == 109.0
