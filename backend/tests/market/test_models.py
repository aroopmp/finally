"""Tests for the Quote model."""

from app.market.models import Quote, SourceKind


def make(price, prev_price, prev_close=None, as_of=1000.0):
    return Quote("AAPL", price, prev_price, prev_close, as_of, as_of, SourceKind.SIMULATOR)


def test_direction_vs_previous_tick():
    assert make(101, 100).direction == "up"
    assert make(99, 100).direction == "down"
    assert make(100, 100).direction == "flat"


def test_day_change_uses_prev_close_not_previous_tick():
    # Up on the tick, but down on the day: the two baselines must differ.
    q = make(price=99.0, prev_price=98.0, prev_close=100.0)
    assert q.direction == "up"
    assert q.change == 1.0
    assert q.day_change == -1.0
    assert q.day_change_pct == -1.0


def test_day_change_none_without_baseline():
    q = make(100, 100, prev_close=None)
    assert q.day_change is None
    assert q.day_change_pct is None


def test_age():
    assert make(1, 1, as_of=1000.0).age(now=1004.0) == 4.0


def test_to_dict_fields():
    d = make(101, 100, prev_close=100).to_dict()
    assert d["ticker"] == "AAPL"
    assert d["source"] == "simulator"
    assert d["direction"] == "up"
    assert d["day_change_pct"] == 1.0
    assert d["as_of"] == 1000.0
    assert set(d) >= {"price", "prev_price", "prev_close", "change", "day_change"}
