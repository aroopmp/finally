"""AI chat assistant: structured-output LLM call, auto-execution of its actions, history."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .config import CHAT_HISTORY_LIMIT, llm_mock
from .db import read, transaction
from .errors import ApiError
from .schema import new_id, now_iso
from .services import portfolio as portfolio_service
from .services import watchlist as watchlist_service
from .services.trading import execute_trade
from .state import AppState

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}
LLM_TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = """You are FinAlly, an AI trading assistant inside a simulated trading workstation \
(fake money, market orders only, instant fills at the current price, no fees).

You can: analyze the user's portfolio (composition, concentration, P&L), suggest trades with brief \
reasoning, execute trades when the user asks or agrees, and manage the watchlist.

Rules:
- Be concise and data-driven. Use the portfolio context provided; never invent prices or holdings.
- Only include a trade when the user asked for it or clearly agreed to your suggestion. \
Suggestions alone belong in "message" with empty arrays.
- Tickers are 1-5 letters. Quantities may be fractional (up to 4 decimals, minimum 0.01).
- Trades run in order and then watchlist changes; each is validated independently, and the real \
outcome is reported to the user separately, so do not claim success in your message.
- Always respond with valid JSON matching the schema: "message" (string), "trades" (array, may be \
empty), "watchlist_changes" (array, may be empty)."""

CANNED_REPLY = (
    "I'm FinAlly, your trading assistant. Try 'buy 10 AAPL', 'sell 5 TSLA', 'add PYPL' "
    "or 'remove NFLX'."
)

_TRADE_RE = re.compile(r"^\s*(buy|sell)\s+(\d*\.?\d+)\s+([A-Za-z]{1,5})\b", re.IGNORECASE)
_WATCH_RE = re.compile(r"^\s*(add|remove)\s+([A-Za-z]{1,5})\b", re.IGNORECASE)


class ChatTrade(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float


class WatchlistChange(BaseModel):
    ticker: str
    action: Literal["add", "remove"]


class LLMResponse(BaseModel):
    """Schema sent to the model (all fields required for strict structured outputs)."""

    message: str
    trades: list[ChatTrade]
    watchlist_changes: list[WatchlistChange]


class LLMResponseLenient(LLMResponse):
    """Schema used to parse the reply: missing arrays default to empty."""

    trades: list[ChatTrade] = Field(default_factory=list)
    watchlist_changes: list[WatchlistChange] = Field(default_factory=list)


# --- LLM ---


def mock_response(message: str) -> LLMResponse:
    """Deterministic reply for LLM_MOCK=true (see PLAN.md §9)."""
    if m := _TRADE_RE.match(message):
        side, qty, ticker = m.group(1).lower(), float(m.group(2)), m.group(3).upper()
        return LLMResponse(
            message=f"Okay, placing an order to {side} {m.group(2)} {ticker}.",
            trades=[ChatTrade(ticker=ticker, side=side, quantity=qty)],
            watchlist_changes=[],
        )
    if m := _WATCH_RE.match(message):
        action, ticker = m.group(1).lower(), m.group(2).upper()
        return LLMResponse(
            message=f"Okay, I'll {action} {ticker} {'to' if action == 'add' else 'from'} your watchlist.",
            trades=[],
            watchlist_changes=[WatchlistChange(ticker=ticker, action=action)],
        )
    return LLMResponse(message=CANNED_REPLY, trades=[], watchlist_changes=[])


def call_llm(messages: list[dict]) -> LLMResponse:
    """Blocking LiteLLM -> OpenRouter -> Cerebras call with structured output."""
    from litellm import completion  # imported lazily: slow to import

    response = completion(
        model=MODEL,
        messages=messages,
        response_format=LLMResponse,
        reasoning_effort="low",
        extra_body=EXTRA_BODY,
        timeout=LLM_TIMEOUT_SECONDS,
    )
    return LLMResponseLenient.model_validate_json(response.choices[0].message.content)


# --- Context and history ---


def build_context(state: AppState) -> str:
    pf = portfolio_service.get_portfolio(state)
    watch = [
        {"ticker": w["ticker"], "price": w["quote"]["price"] if w["quote"] else None}
        for w in watchlist_service.list_watchlist(state)
    ]
    context = {
        "cash_balance": pf["cash_balance"],
        "total_value": pf["total_value"],
        "unrealized_pnl": pf["unrealized_pnl"],
        "positions": [
            {
                k: p[k]
                for k in (
                    "ticker",
                    "quantity",
                    "avg_cost",
                    "price",
                    "market_value",
                    "unrealized_pnl",
                    "unrealized_pnl_pct",
                )
            }
            for p in pf["positions"]
        ],
        "watchlist": watch,
        "price_source": pf["market"]["source"],
    }
    return json.dumps(context)


def _action_summary(action: dict) -> str:
    if action["type"] == "trade":
        what = f"{action['side']} {action['quantity']} {action['ticker']}"
        if action["status"] == "ok":
            return f"{what}: filled at {action['price']}"
        return f"{what}: FAILED ({action['error']['message']})"
    what = f"{action['action']} {action['ticker']}"
    return (
        f"{what}: ok"
        if action["status"] == "ok"
        else f"{what}: FAILED ({action['error']['message']})"
    )


def load_history(state: AppState, limit: int = CHAT_HISTORY_LIMIT) -> list[dict]:
    with read(state.db_path) as conn:
        rows = conn.execute(
            "SELECT id, role, content, actions, created_at FROM chat_messages "
            "ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "actions": json.loads(r["actions"]) if r["actions"] else None,
            "created_at": r["created_at"],
        }
        for r in reversed(rows)
    ]


def _llm_history(state: AppState) -> list[dict]:
    out = []
    for m in load_history(state):
        content = m["content"]
        if m["actions"]:
            # Real server-side outcomes, so failures are not hidden behind the model's prose.
            content += (
                "\n[Action results: " + "; ".join(_action_summary(a) for a in m["actions"]) + "]"
            )
        out.append({"role": m["role"], "content": content})
    return out


# --- Execution ---


def _error(exc: ApiError) -> dict:
    return {"code": exc.code, "message": exc.message}


async def _run_actions(state: AppState, llm: LLMResponse) -> list[dict]:
    """Execute trades in order, then watchlist changes. One failure does not stop the rest."""
    results: list[dict] = []
    for t in llm.trades:
        base = {
            "type": "trade",
            "ticker": t.ticker.strip().upper(),
            "side": t.side,
            "quantity": t.quantity,
        }
        try:
            r = await execute_trade(state, t.ticker, t.quantity, t.side)
            results.append(
                {
                    **base,
                    "status": "ok",
                    "price": r["trade"]["price"],
                    "total": r["trade"]["total"],
                    "trade_id": r["trade"]["id"],
                }
            )
        except ApiError as exc:
            results.append({**base, "status": "error", "error": _error(exc)})
    for w in llm.watchlist_changes:
        base = {"type": "watchlist", "ticker": w.ticker.strip().upper(), "action": w.action}
        try:
            if w.action == "add":
                await watchlist_service.add_ticker(state, w.ticker)
            else:
                if not await watchlist_service.remove_ticker(state, w.ticker):
                    raise ApiError(
                        404, "not_in_watchlist", f"{base['ticker']} is not on the watchlist"
                    )
            results.append({**base, "status": "ok"})
        except ApiError as exc:
            results.append({**base, "status": "error", "error": _error(exc)})
    return results


async def handle_chat(state: AppState, message: str) -> dict:
    message = message.strip()
    if not message:
        raise ApiError(400, "empty_message", "Message must not be empty")
    if not llm_mock() and not os.environ.get("OPENROUTER_API_KEY", "").strip():
        raise ApiError(
            503, "chat_unavailable", "Chat is unavailable: OPENROUTER_API_KEY is not set"
        )

    epoch = state.epoch
    if llm_mock():
        llm = mock_response(message)
    else:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": "Current portfolio context (JSON): " + build_context(state),
            },
            *_llm_history(state),
            {"role": "user", "content": message},
        ]
        try:
            llm = await asyncio.to_thread(call_llm, messages)
        except (ValidationError, ValueError) as exc:
            logger.warning("Malformed LLM output: %s", exc)
            raise ApiError(
                502, "llm_error", "The assistant returned an invalid response. Please try again."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - provider/network/timeout errors
            logger.warning("LLM call failed: %s", exc)
            raise ApiError(
                502, "llm_error", "The assistant is unavailable right now. Please try again."
            ) from exc

    async with state.lock:
        if state.epoch != epoch:
            raise ApiError(
                409, "account_reset", "The account was reset while the assistant was thinking"
            )
        actions = await _run_actions(state, llm)
        user_id, assistant_id, now = new_id(), new_id(), now_iso()
        with transaction(state.db_path) as conn:
            conn.execute(
                "INSERT INTO chat_messages (id, role, content, actions, created_at) VALUES (?, 'user', ?, NULL, ?)",
                (user_id, message, now),
            )
            conn.execute(
                "INSERT INTO chat_messages (id, role, content, actions, created_at) VALUES (?, 'assistant', ?, ?, ?)",
                (assistant_id, llm.message, json.dumps(actions), now_iso()),
            )
    return {
        "id": assistant_id,
        "role": "assistant",
        "content": llm.message,
        "actions": actions,
        "created_at": now,
    }
