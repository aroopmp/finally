"""Chat tests: mock mode, action results, failure handling, reset race."""

import pytest

from app import chat as chat_module
from app.chat import mock_response
from tests.conftest import buy


def say(client, message):
    return client.post("/api/chat", json={"message": message})


class TestMockRule:
    def test_trade(self):
        r = mock_response("buy 10 aapl")
        assert r.trades[0].model_dump() == {"ticker": "AAPL", "side": "buy", "quantity": 10.0}
        assert mock_response("SELL 2.5 tsla").trades[0].side == "sell"

    def test_watchlist(self):
        assert mock_response("add pypl").watchlist_changes[0].model_dump() == {
            "ticker": "PYPL",
            "action": "add",
        }
        assert mock_response("remove NFLX").watchlist_changes[0].action == "remove"

    def test_other_is_canned(self):
        r = mock_response("how am I doing?")
        assert (
            r.trades == [] and r.watchlist_changes == [] and r.message == chat_module.CANNED_REPLY
        )


def test_chat_trade_executes_and_reports(client):
    body = say(client, "buy 10 AAPL").json()
    action = body["actions"][0]
    assert action["type"] == "trade" and action["status"] == "ok" and action["price"] == 100.0
    assert client.get("/api/portfolio").json()["cash_balance"] == 9000.0


def test_chat_failed_trade_reported_per_action(client):
    body = say(client, "buy 1000 AAPL").json()
    action = body["actions"][0]
    assert action["status"] == "error" and action["error"]["code"] == "insufficient_cash"
    assert client.get("/api/portfolio").json()["cash_balance"] == 10000.0


def test_chat_watchlist_changes(client):
    assert say(client, "add PYPL").json()["actions"][0]["status"] == "ok"
    assert say(client, "remove PYPL").json()["actions"][0]["status"] == "ok"
    gone = say(client, "remove PYPL").json()["actions"][0]
    assert gone["status"] == "error" and gone["error"]["code"] == "not_in_watchlist"
    assert say(client, "add ZZZZ").json()["actions"][0]["error"]["code"] == "unknown_ticker"


def test_history_is_persisted_in_order(client):
    say(client, "hello")
    say(client, "buy 1 AAPL")
    msgs = client.get("/api/chat/history").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[3]["actions"][0]["status"] == "ok" and msgs[0]["actions"] is None


def test_empty_message_rejected(client):
    assert say(client, "   ").status_code == 400


@pytest.fixture
def real_llm(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


def test_no_key_means_chat_unavailable_but_trading_works(client, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    r = say(client, "hello")
    assert r.status_code == 503 and r.json()["error"]["code"] == "chat_unavailable"
    assert buy(client, "AAPL", 1).status_code == 200


def test_llm_failure_executes_nothing_and_stores_nothing(client, real_llm, monkeypatch):
    def boom(messages):
        raise TimeoutError("slow")

    monkeypatch.setattr(chat_module, "call_llm", boom)
    r = say(client, "buy 10 AAPL")
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_error"
    assert client.get("/api/chat/history").json()["messages"] == []
    assert client.get("/api/portfolio").json()["positions"] == []


def test_malformed_llm_output_is_plain_error(client, real_llm, monkeypatch):
    def bad(messages):
        chat_module.LLMResponseLenient.model_validate_json("{not json")

    monkeypatch.setattr(chat_module, "call_llm", bad)
    assert say(client, "hi").status_code == 502


def test_real_path_builds_context_and_includes_action_results(client, real_llm, monkeypatch):
    seen = []

    def fake(messages):
        seen.append(messages)
        return chat_module.LLMResponse(
            message="Done",
            trades=[chat_module.ChatTrade(ticker="AAPL", side="buy", quantity=1)],
            watchlist_changes=[],
        )

    monkeypatch.setattr(chat_module, "call_llm", fake)
    say(client, "buy one AAPL")
    say(client, "and again")
    first, second = seen
    assert first[0]["role"] == "system" and "FinAlly" in first[0]["content"]
    assert '"cash_balance": 10000.0' in first[1]["content"]
    assert first[-1] == {"role": "user", "content": "buy one AAPL"}
    joined = " ".join(m["content"] for m in second)
    assert "[Action results: buy 1.0 AAPL: filled at 100.0]" in joined


def test_history_limited_to_last_20_messages(client, real_llm, monkeypatch):
    seen = []

    def fake(messages):
        seen.append(messages)
        return chat_module.LLMResponse(message="ok", trades=[], watchlist_changes=[])

    monkeypatch.setattr(chat_module, "call_llm", fake)
    for i in range(14):
        say(client, f"m{i}")
    history = [m for m in seen[-1] if m["role"] in ("user", "assistant")]
    assert len(history) == 20 + 1  # 20 stored messages + the new user message


def test_response_arriving_after_reset_is_discarded(client, real_llm, monkeypatch):
    state = client.app_state

    def reset_midway(messages):
        state.epoch += 1  # what POST /api/reset does while the LLM is thinking
        return chat_module.LLMResponse(
            message="late",
            trades=[chat_module.ChatTrade(ticker="AAPL", side="buy", quantity=1)],
            watchlist_changes=[],
        )

    monkeypatch.setattr(chat_module, "call_llm", reset_midway)
    r = say(client, "buy AAPL")
    assert r.status_code == 409 and r.json()["error"]["code"] == "account_reset"
    assert client.get("/api/portfolio").json()["positions"] == []
    assert client.get("/api/chat/history").json()["messages"] == []
