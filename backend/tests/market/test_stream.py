"""SSE stream contract tests against the real event generator."""

import json
from types import SimpleNamespace

import pytest

from app.market.cache import PriceCache
from app.market.stream import _generate_events


class FakeRequest:
    client = SimpleNamespace(host="test")

    def __init__(self, disconnect_after: int):
        self.calls = 0
        self.limit = disconnect_after

    async def is_disconnected(self):
        self.calls += 1
        return self.calls > self.limit


async def collect(cache, polls=1):
    out = []
    async for chunk in _generate_events(cache, FakeRequest(polls), interval=0):
        out.append(chunk)
    return out


@pytest.mark.asyncio
async def test_retry_then_initial_snapshot_on_connect():
    cache = PriceCache(clock=lambda: 100.0)
    cache.update("AAPL", 190.5, prev_close=189.0)
    chunks = await collect(cache)
    assert chunks[0] == "retry: 1000\n\n"
    payload = json.loads(chunks[1].removeprefix("data: "))
    assert payload["AAPL"]["price"] == 190.5
    assert payload["AAPL"]["day_change_pct"] == pytest.approx(0.7937, abs=1e-3)
    assert payload["AAPL"]["as_of"] == 100.0  # unix seconds, numeric
    assert payload["AAPL"]["source"] == "simulator"


@pytest.mark.asyncio
async def test_empty_cache_sends_empty_object():
    chunks = await collect(PriceCache())
    assert chunks[1] == "data: {}\n\n"
