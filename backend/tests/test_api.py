"""API route tests using FastAPI TestClient (with lifespan and the real simulator)."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "api.db", snapshot_interval=3600)
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_initial_portfolio(client):
    p = client.get("/api/portfolio").json()
    assert p["cash_balance"] == 10000.0
    assert p["positions"] == []
    assert p["total_value"] == 10000.0


def test_default_watchlist_has_prices(client):
    items = client.get("/api/watchlist").json()
    assert len(items) == 10
    assert all(i["price"] for i in items)


def test_buy_and_sell_flow(client):
    r = client.post("/api/portfolio/trade", json={"ticker": "aapl", "quantity": 2, "side": "buy"})
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "AAPL" and body["cash_balance"] < 10000.0
    p = client.get("/api/portfolio").json()
    assert p["positions"][0]["quantity"] == 2
    r = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 2, "side": "sell"})
    assert r.status_code == 200
    assert client.get("/api/portfolio").json()["positions"] == []
    assert len(client.get("/api/portfolio/history").json()) == 3


def test_trade_errors(client):
    post = lambda **kw: client.post("/api/portfolio/trade", json=kw)  # noqa: E731
    assert post(ticker="AAPL", quantity=1e9, side="buy").status_code == 400
    assert post(ticker="AAPL", quantity=1, side="sell").status_code == 400
    assert post(ticker="AAPL", quantity=0, side="buy").status_code == 400
    assert post(ticker="AAPL", quantity=1, side="hold").status_code == 400
    assert post(ticker="ZZZZ", quantity=1, side="buy").status_code == 400
    assert post(ticker="123", quantity=1, side="buy").status_code == 400
    assert client.post("/api/portfolio/trade", json={"ticker": "AAPL"}).status_code == 422


def test_trade_on_untracked_supported_ticker_not_in_default_set(client):
    # Remove from watchlist, then trade: price is fetched on demand and stays tracked.
    assert client.delete("/api/watchlist/NFLX").status_code == 200
    r = client.post("/api/portfolio/trade", json={"ticker": "NFLX", "quantity": 1, "side": "buy"})
    assert r.status_code == 200
    tickers = [i["ticker"] for i in client.get("/api/watchlist").json()]
    assert "NFLX" not in tickers
    assert client.get("/api/portfolio").json()["positions"][0]["current_price"] > 0


def test_watchlist_add_remove(client):
    assert client.delete("/api/watchlist/TSLA").status_code == 200
    assert client.delete("/api/watchlist/TSLA").status_code == 404
    assert "TSLA" not in [i["ticker"] for i in client.get("/api/watchlist").json()]
    r = client.post("/api/watchlist", json={"ticker": " tsla "})
    assert r.status_code == 200 and r.json()["ticker"] == "TSLA" and r.json()["price"]
    # idempotent
    assert client.post("/api/watchlist", json={"ticker": "TSLA"}).status_code == 200
    assert len(client.get("/api/watchlist").json()) == 10


def test_watchlist_rejects_unknown_and_invalid(client):
    assert client.post("/api/watchlist", json={"ticker": "ZZZZ"}).status_code == 400
    assert client.post("/api/watchlist", json={"ticker": "bad!"}).status_code == 400
    assert client.delete("/api/watchlist/bad!").status_code == 400


def test_reset(client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
    client.delete("/api/watchlist/V")
    assert client.post("/api/reset").status_code == 200
    p = client.get("/api/portfolio").json()
    assert p["cash_balance"] == 10000.0 and p["positions"] == []
    assert len(client.get("/api/watchlist").json()) == 10
    assert len(client.get("/api/portfolio/history").json()) == 1


def test_state_persists_across_app_restart(tmp_path):
    path = tmp_path / "persist.db"
    with TestClient(create_app(db_path=path, snapshot_interval=3600)) as c:
        c.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
        c.delete("/api/watchlist/AAPL")
    with TestClient(create_app(db_path=path, snapshot_interval=3600)) as c:
        p = c.get("/api/portfolio").json()
        assert p["positions"][0]["ticker"] == "AAPL"
        assert p["positions"][0]["current_price"] > 0  # held ticker still priced
