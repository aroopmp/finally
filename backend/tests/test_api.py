"""API tests: fresh start, trading, watchlist, history, reset, health."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import trading as trading_module
from tests.conftest import buy


def portfolio(client):
    return client.get("/api/portfolio").json()


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["market"]["status"] == "ok"


def test_fresh_start(client):
    pf = portfolio(client)
    assert pf["cash_balance"] == 10000.0 and pf["total_value"] == 10000.0
    assert pf["positions"] == []
    wl = client.get("/api/watchlist").json()["items"]
    assert [w["ticker"] for w in wl] == [
        "AAPL",
        "GOOGL",
        "MSFT",
        "AMZN",
        "TSLA",
        "NVDA",
        "META",
        "JPM",
        "V",
        "NFLX",
    ]
    assert all(w["quote"]["price"] > 0 for w in wl)
    hist = client.get("/api/portfolio/history").json()["snapshots"]
    assert [h["total_value"] for h in hist] == [10000.0]


class TestBuySell:
    def test_buy_reduces_cash_and_creates_position(self, client):
        r = buy(client, "AAPL", 10)
        assert r.status_code == 200
        body = r.json()
        assert body["trade"]["price"] == 100.0 and body["trade"]["total"] == 1000.0
        assert body["cash_balance"] == 9000.0
        pf = portfolio(client)
        assert pf["positions"][0]["ticker"] == "AAPL" and pf["positions"][0]["quantity"] == 10
        assert pf["total_value"] == 10000.0

    def test_ticker_normalised(self, client):
        assert buy(client, " aapl ", 1).json()["trade"]["ticker"] == "AAPL"

    def test_average_cost_on_second_buy(self, client, source):
        buy(client, "AAPL", 10)
        source.set_price("AAPL", 200.0)
        buy(client, "AAPL", 10)
        pos = portfolio(client)["positions"][0]
        assert pos["quantity"] == 20 and pos["avg_cost"] == 150.0

    def test_partial_sell_realises_cash_and_keeps_avg_cost(self, client, source):
        buy(client, "AAPL", 10)
        source.set_price("AAPL", 110.0)
        r = buy(client, "AAPL", 4, side="sell").json()
        assert r["cash_balance"] == 9000.0 + 440.0
        assert r["position"]["quantity"] == 6 and r["position"]["avg_cost"] == 100.0

    def test_sell_all_deletes_position(self, client):
        buy(client, "AAPL", 10)
        r = buy(client, "AAPL", 10, side="sell").json()
        assert r["position"] is None
        assert portfolio(client)["positions"] == []
        assert r["cash_balance"] == 10000.0

    def test_sell_at_a_loss(self, client, source):
        buy(client, "AAPL", 10)
        source.set_price("AAPL", 90.0)
        assert buy(client, "AAPL", 10, side="sell").json()["cash_balance"] == 9900.0

    def test_insufficient_cash(self, client):
        r = buy(client, "AAPL", 101)
        assert r.status_code == 400 and r.json()["error"]["code"] == "insufficient_cash"
        assert portfolio(client)["cash_balance"] == 10000.0

    def test_insufficient_shares(self, client):
        buy(client, "AAPL", 5)
        r = buy(client, "AAPL", 6, side="sell")
        assert r.status_code == 400 and r.json()["error"]["code"] == "insufficient_shares"
        assert buy(client, "MSFT", 1, side="sell").json()["error"]["code"] == "insufficient_shares"

    @pytest.mark.parametrize("qty", [0, -1, 0.00001, "abc", "nan", "inf", 1.23456])
    def test_invalid_quantity(self, client, qty):
        r = buy(client, "AAPL", qty)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_quantity"

    def test_minimum_order_enforced_on_buy(self, client):
        assert buy(client, "AAPL", 0.005).status_code == 400
        assert buy(client, "AAPL", 0.01).status_code == 200

    def test_fractional_remainder_below_minimum_can_be_liquidated(self, client):
        buy(client, "AAPL", 0.0101)
        assert buy(client, "AAPL", 0.01, side="sell").json()["position"]["quantity"] == 0.0001
        # Selling the dust remainder is allowed even though it is below the minimum order.
        r = buy(client, "AAPL", 0.0001, side="sell")
        assert r.status_code == 200 and r.json()["position"] is None
        # But a partial sell below the minimum is not.
        buy(client, "AAPL", 1)
        assert buy(client, "AAPL", 0.005, side="sell").status_code == 400

    def test_cash_rounded_to_cents(self, client, source):
        source.set_price("AAPL", 33.33)
        assert buy(client, "AAPL", 0.5).json()["cash_balance"] == 10000 - 16.67  # 16.665 -> 16.67

    def test_unknown_and_malformed_tickers_rejected(self, client):
        assert buy(client, "ZZZZ", 1).json()["error"]["code"] == "unknown_ticker"
        assert buy(client, "TOOLONGX", 1).json()["error"]["code"] == "invalid_ticker"
        assert buy(client, "AA1", 1).status_code == 400

    def test_bad_side_and_missing_fields(self, client):
        r = client.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "hold"}
        )
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
        assert client.post("/api/portfolio/trade", json={}).status_code == 400

    def test_trade_in_untracked_ticker_then_tracked_while_held(self, client, source):
        assert "PYPL" not in source.get_tickers()
        assert buy(client, "PYPL", 2).status_code == 200
        assert "PYPL" in source.get_tickers()
        buy(client, "PYPL", 2, side="sell")
        assert "PYPL" not in source.get_tickers()

    def test_failed_buy_of_untracked_ticker_is_not_left_tracked(self, client, source):
        assert buy(client, "PYPL", 1_000_000).status_code == 400
        assert "PYPL" not in source.get_tickers()

    def test_trade_records_snapshot(self, client):
        buy(client, "AAPL", 10)
        assert len(client.get("/api/portfolio/history").json()["snapshots"]) == 2

    def test_missing_price_blocks_trade(self, client, source):
        source.refresh_on_add = False
        client.app_state.cache.remove("PYPL")
        r = buy(client, "PYPL", 1)
        assert r.status_code == 503 and r.json()["error"]["code"] == "quote_unavailable"


class TestAtomicity:
    def test_concurrent_buys_cannot_overspend(self, client):
        def go(_):
            return buy(client, "AAPL", 60).status_code  # 60 * 100 = 6000 each; only one fits

        with ThreadPoolExecutor(4) as pool:
            codes = sorted(pool.map(go, range(4)))
        assert codes == [200, 400, 400, 400]
        pf = portfolio(client)
        assert pf["cash_balance"] == 4000.0 and pf["positions"][0]["quantity"] == 60

    def test_failure_mid_transaction_rolls_back(self, client, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(trading_module, "total_value", boom)
        with pytest.raises(RuntimeError):
            buy(client, "AAPL", 10)
        monkeypatch.undo()
        pf = portfolio(client)
        assert pf["cash_balance"] == 10000.0 and pf["positions"] == []
        from app.db import read

        with read(client.app_state.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


class TestWatchlist:
    def test_add_remove(self, client):
        r = client.post("/api/watchlist", json={"ticker": "pypl"})
        assert r.status_code == 201 and r.json()["ticker"] == "PYPL"
        assert "PYPL" in [w["ticker"] for w in client.get("/api/watchlist").json()["items"]]
        assert client.delete("/api/watchlist/PYPL").status_code == 200
        assert "PYPL" not in [w["ticker"] for w in client.get("/api/watchlist").json()["items"]]

    def test_add_is_idempotent(self, client):
        client.post("/api/watchlist", json={"ticker": "PYPL"})
        assert client.post("/api/watchlist", json={"ticker": "PYPL"}).status_code == 201
        tickers = [w["ticker"] for w in client.get("/api/watchlist").json()["items"]]
        assert tickers.count("PYPL") == 1

    def test_unknown_ticker_rejected(self, client):
        assert (
            client.post("/api/watchlist", json={"ticker": "ZZZZ"}).json()["error"]["code"]
            == "unknown_ticker"
        )
        assert client.post("/api/watchlist", json={"ticker": "1"}).status_code == 400

    def test_remove_missing_is_404(self, client):
        assert client.delete("/api/watchlist/PYPL").status_code == 404

    def test_held_ticker_stays_priced_after_removal(self, client, source):
        buy(client, "AAPL", 1)
        client.delete("/api/watchlist/AAPL")
        assert "AAPL" in source.get_tickers()
        assert portfolio(client)["positions"][0]["price"] == 100.0
        buy(client, "AAPL", 1, side="sell")
        assert "AAPL" not in source.get_tickers()


def test_history_and_value_follow_prices(client, source):
    buy(client, "AAPL", 10)
    source.set_price("AAPL", 120.0)
    pf = portfolio(client)
    assert pf["unrealized_pnl"] == 200.0 and pf["total_value"] == 10200.0
    assert pf["positions"][0]["unrealized_pnl_pct"] == 20.0


def test_reset(client, source):
    buy(client, "AAPL", 10)
    client.post("/api/watchlist", json={"ticker": "PYPL"})
    client.delete("/api/watchlist/NFLX")
    client.post("/api/chat", json={"message": "hello"})
    assert client.post("/api/reset").status_code == 200
    pf = portfolio(client)
    assert pf["cash_balance"] == 10000.0 and pf["positions"] == []
    assert len(client.get("/api/watchlist").json()["items"]) == 10
    assert client.get("/api/chat/history").json()["messages"] == []
    assert len(client.get("/api/portfolio/history").json()["snapshots"]) == 1
    assert sorted(source.get_tickers()) == sorted(
        w["ticker"] for w in client.get("/api/watchlist").json()["items"]
    )


def test_snapshot_skipped_when_price_missing(client):
    from app.services.portfolio import record_snapshot

    state = client.app_state
    buy(client, "AAPL", 1)
    before = len(client.get("/api/portfolio/history").json()["snapshots"])
    state.cache.remove("AAPL")
    assert record_snapshot(state) is False
    assert len(client.get("/api/portfolio/history").json()["snapshots"]) == before
    pf = portfolio(client)
    assert pf["prices_complete"] is False and pf["positions"][0]["price"] is None
    assert pf["positions"][0]["market_value"] == 100.0  # falls back to cost basis, not zero


class TestPriceEligibility:
    def test_holding_without_price_is_valued_at_cost_never_zero(self, client):
        buy(client, "AAPL", 10)
        client.app_state.cache.remove("AAPL")
        p = client.get("/api/portfolio").json()
        pos = p["positions"][0]
        assert pos["price"] is None and pos["stale"] is True
        assert pos["market_value"] == 1000.0 and pos["unrealized_pnl"] is None
        assert p["total_value"] == 10000.0 and p["prices_complete"] is False

    def test_incomplete_prices_never_persist_a_snapshot(self, client):
        buy(client, "AAPL", 10)
        before = len(client.get("/api/portfolio/history").json()["snapshots"])
        client.app_state.cache.remove("AAPL")
        assert portfolio_record(client) is False
        assert len(client.get("/api/portfolio/history").json()["snapshots"]) == before

    def test_sell_without_current_quote_is_refused_not_filled_at_zero(self, client, source):
        buy(client, "PYPL", 2)
        client.app_state.cache.remove("PYPL")
        source.refresh_on_add = False
        r = buy(client, "PYPL", 2, side="sell")
        assert r.status_code == 503
        assert client.get("/api/portfolio").json()["positions"][0]["quantity"] == 2


def portfolio_record(client) -> bool:
    from app.services import portfolio

    return portfolio.record_snapshot(client.app_state)


def test_sse_payload_contract(client):
    """Documented in API.md: a full snapshot keyed by ticker, Unix-second float timestamps."""
    from app.market.stream import _generate_events

    class Req:
        client = None

        async def is_disconnected(self):
            return False

    async def first_data():
        gen = _generate_events(client.app_state.cache, Req(), interval=0.01)
        async for chunk in gen:
            if chunk.startswith("data:"):
                await gen.aclose()
                return json.loads(chunk[5:])

    import asyncio
    import json

    data = asyncio.run(first_data())
    assert set(data) >= {"AAPL", "MSFT"}
    q = data["AAPL"]
    assert set(q) == {
        "ticker", "price", "prev_price", "prev_close", "change", "direction",
        "day_change", "day_change_pct", "as_of", "source",
    }  # fmt: skip
    assert q["ticker"] == "AAPL" and isinstance(q["as_of"], float) and q["as_of"] < 1e11
