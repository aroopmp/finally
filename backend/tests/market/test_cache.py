"""Tests for PriceCache."""

import math

from app.market.cache import PriceCache
from app.market.models import SourceKind


def test_update_and_get():
    cache = PriceCache()
    q = cache.update("AAPL", 190.5, prev_close=189.0)
    assert q.price == 190.5
    assert q.prev_close == 189.0
    assert cache.get("AAPL") == q
    assert cache.get_price("AAPL") == 190.5
    assert "AAPL" in cache and len(cache) == 1


def test_first_update_is_flat():
    q = PriceCache().update("AAPL", 100.0)
    assert q.prev_price == 100.0
    assert q.direction == "flat"


def test_previous_price_tracks_last_tick():
    cache = PriceCache()
    cache.update("AAPL", 100.0)
    q = cache.update("AAPL", 101.0)
    assert q.prev_price == 100.0
    assert q.direction == "up"


def test_prev_close_survives_later_updates():
    cache = PriceCache()
    cache.update("AAPL", 100.0, prev_close=98.0)
    q = cache.update("AAPL", 101.0)
    assert q.prev_close == 98.0


def test_rejects_bad_prices_and_keeps_old_quote():
    cache = PriceCache()
    cache.update("AAPL", 100.0)
    version = cache.version
    for bad in (0, -1, math.nan, math.inf, None):
        assert cache.update("AAPL", bad) is None
    assert cache.get_price("AAPL") == 100.0
    assert cache.version == version


def test_prices_rounded_to_cents():
    assert PriceCache().update("AAPL", 100.12345).price == 100.12


def test_remove_bumps_version_and_clears_baseline():
    cache = PriceCache()
    cache.update("AAPL", 100.0, prev_close=90.0)
    version = cache.version
    cache.remove("AAPL")
    assert cache.version == version + 1
    assert cache.get("AAPL") is None
    assert cache.update("AAPL", 100.0).prev_close is None
    cache.remove("NOPE")  # no-op


def test_get_fresh_honours_age():
    now = [1000.0]
    cache = PriceCache(clock=lambda: now[0])
    cache.update("AAPL", 100.0)
    assert cache.get_fresh("AAPL", 5) is not None
    now[0] = 1010.0
    assert cache.get_fresh("AAPL", 5) is None
    assert cache.get_fresh("AAPL", None) is not None  # never stale
    assert cache.get_fresh("MISSING", 5) is None


def test_source_and_as_of_recorded():
    cache = PriceCache(clock=lambda: 50.0)
    q = cache.update("AAPL", 100.0, as_of=42.0, source=SourceKind.MASSIVE_DELAYED)
    assert q.as_of == 42.0 and q.fetched_at == 50.0
    assert q.source is SourceKind.MASSIVE_DELAYED


def test_version_increments_and_clear():
    cache = PriceCache()
    v = cache.version
    cache.update("A", 1.0)
    cache.update("B", 2.0)
    assert cache.version == v + 2
    cache.clear()
    assert len(cache) == 0 and cache.version == v + 3
