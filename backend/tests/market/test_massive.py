"""Tests for MassiveDataSource using real SDK model objects (not MagicMock)."""

from datetime import date
from unittest.mock import MagicMock

import pytest
from massive.rest.models import GroupedDailyAgg, TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import (
    Mode,
    MassiveDataSource,
    NotAuthorized,
    classify_error,
    pick_price,
)
from app.market.models import QuoteUnavailable, SourceKind, SourceStatus, UnknownTicker

NS = 1_700_000_000_000_000_000  # 2023-11-14 in nanoseconds


def snap(ticker="AAPL", trade=190.5, minute=None, day=None, prev=189.0, updated=NS):
    d = {"ticker": ticker, "updated": updated}
    if trade is not None:
        d["lastTrade"] = {"p": trade, "s": 10, "t": NS}
    if minute is not None:
        d["min"] = {"c": minute, "t": 1_700_000_000_000}
    if day is not None:
        d["day"] = {"c": day}
    if prev is not None:
        d["prevDay"] = {"c": prev}
    return TickerSnapshot.from_dict(d)


def bar(ticker, close):
    return GroupedDailyAgg.from_dict({"T": ticker, "c": close, "t": 1_700_000_000_000})


def make_source(client=None, plan="delayed", now=1_700_100_000.0):
    cache = PriceCache()
    src = MassiveDataSource("key", cache, poll_interval=60, plan=plan,
                            client=client or MagicMock(), clock=lambda: now)
    return src, cache


class TestPickPrice:
    def test_prefers_last_trade_and_converts_ns(self):
        price, as_of = pick_price(snap(trade=190.5))
        assert price == 190.5
        assert as_of == pytest.approx(1_700_000_000.0)

    def test_falls_back_to_minute_close_ms(self):
        price, as_of = pick_price(snap(trade=None, minute=191.0))
        assert price == 191.0 and as_of == pytest.approx(1_700_000_000.0)

    def test_falls_back_to_day_then_prev_close(self):
        assert pick_price(snap(trade=None, day=192.0))[0] == 192.0
        assert pick_price(snap(trade=None, day=0, prev=189.0))[0] == 189.0

    def test_none_when_nothing_usable(self):
        assert pick_price(snap(trade=None, prev=None)) is None
        assert pick_price(snap(trade=0, prev=0)) is None


@pytest.mark.asyncio
class TestLiveMode:
    async def test_poll_updates_cache_with_baseline(self):
        src, cache = make_source()
        src._tickers = ["AAPL", "MSFT"]
        src._client.get_snapshot_all.return_value = [
            snap("AAPL", 190.5, prev=189.0), snap("MSFT", 420.0, prev=None)]
        await src._poll_once()
        aapl = cache.get("AAPL")
        assert aapl.price == 190.5 and aapl.prev_close == 189.0
        assert aapl.source is SourceKind.MASSIVE_DELAYED
        assert aapl.as_of == pytest.approx(1_700_000_000.0)
        assert cache.get("MSFT").prev_close is None
        assert src.status is SourceStatus.OK

    async def test_realtime_plan_kind(self):
        src, cache = make_source(plan="realtime")
        src._tickers = ["AAPL"]
        src._client.get_snapshot_all.return_value = [snap()]
        await src._poll_once()
        assert cache.get("AAPL").source is SourceKind.MASSIVE_REALTIME

    async def test_unusable_snapshot_skipped(self):
        src, cache = make_source()
        src._tickers = ["AAPL", "BAD"]
        src._client.get_snapshot_all.return_value = [
            snap("AAPL"), snap("BAD", trade=None, prev=None)]
        await src._poll_once()
        assert "AAPL" in cache and "BAD" not in cache

    async def test_failed_poll_keeps_old_quote_and_degrades(self):
        src, cache = make_source()
        src._tickers = ["AAPL"]
        src._client.get_snapshot_all.return_value = [snap()]
        await src._poll_once()
        src._client.get_snapshot_all.side_effect = RuntimeError("boom")
        await src._poll_once()
        assert cache.get_price("AAPL") == 190.5
        assert src.status is SourceStatus.DEGRADED

    async def test_bad_key_is_error_status(self):
        src, cache = make_source()
        src._tickers = ["AAPL"]
        src._client.get_snapshot_all.side_effect = Exception('{"message":"Unknown API Key"}')
        await src._poll_once()
        assert src.status is SourceStatus.ERROR
        assert len(cache) == 0

    async def test_add_ticker_fetches_first_quote(self):
        src, cache = make_source()
        src._client.get_snapshot_all.return_value = [snap("PYPL", 65.0)]
        await src.add_ticker("pypl")
        assert cache.get_price("PYPL") == 65.0
        assert "PYPL" in src.get_tickers()

    async def test_add_unpriceable_ticker_raises(self):
        src, _ = make_source()
        src._client.get_snapshot_all.return_value = []
        with pytest.raises(UnknownTicker):
            await src.add_ticker("ZZZZ")
        with pytest.raises(UnknownTicker):
            await src.add_ticker("not a ticker")

    async def test_get_quotes_failure_raises_unavailable(self):
        src, _ = make_source()
        src._client.get_snapshot_all.side_effect = RuntimeError("down")
        with pytest.raises(QuoteUnavailable):
            await src.get_quotes(["AAPL"])

    async def test_remove_ticker(self):
        src, cache = make_source()
        src._tickers = ["AAPL"]
        cache.update("AAPL", 1.0)
        await src.remove_ticker("aapl")
        assert src.get_tickers() == [] and "AAPL" not in cache


@pytest.mark.asyncio
class TestEodMode:
    NOT_ENTITLED = Exception('{"status":"NOT_AUTHORIZED","message":"You are not entitled"}')

    async def test_403_switches_to_eod_and_uses_grouped_daily(self):
        # now = 2023-11-15 (Wed): latest day = Tue 14th, previous = Mon 13th
        src, cache = make_source(now=1_700_052_000.0)
        src._tickers = ["AAPL", "MSFT"]
        src._client.get_snapshot_all.side_effect = self.NOT_ENTITLED

        def grouped(day, adjusted=True):
            return {14: [bar("AAPL", 190.0), bar("MSFT", 420.0), bar("ZZ", 1.0)],
                    13: [bar("AAPL", 188.0)]}.get(day.day, [])

        src._client.get_grouped_daily_aggs.side_effect = grouped
        await src._poll_once()
        assert src._mode is Mode.EOD and src.kind is SourceKind.MASSIVE_EOD
        aapl = cache.get("AAPL")
        assert aapl.price == 190.0 and aapl.prev_close == 188.0
        assert aapl.source is SourceKind.MASSIVE_EOD
        assert cache.get("MSFT").prev_close is None
        assert "ZZ" not in cache
        # Second poll the same day does not hit the API again
        src._client.get_grouped_daily_aggs.reset_mock()
        await src._poll_once()
        src._client.get_grouped_daily_aggs.assert_not_called()

    async def test_eod_walks_back_over_weekend(self):
        # now = Mon 2023-11-13 -> weekend skipped -> Fri 10th
        src, _ = make_source(now=1_699_880_000.0)
        calls = []

        def grouped(day, adjusted=True):
            calls.append(day)
            return [bar("AAPL", 180.0)] if day == date(2023, 11, 10) else []

        src._client.get_grouped_daily_aggs.side_effect = grouped
        closes = await src.get_eod_closes(["AAPL"])
        assert closes == {"AAPL": 180.0}
        assert all(d.weekday() < 5 for d in calls)

    async def test_get_eod_closes_for_date(self):
        src, _ = make_source()
        src._client.get_grouped_daily_aggs.return_value = [bar("AAPL", 150.0), bar("X", 2.0)]
        assert await src.get_eod_closes(["AAPL"], on=date(2023, 1, 3)) == {"AAPL": 150.0}

    async def test_get_quotes_falls_back_to_eod_on_403(self):
        src, _ = make_source(now=1_700_052_000.0)
        src._client.get_snapshot_all.side_effect = self.NOT_ENTITLED
        src._client.get_grouped_daily_aggs.return_value = [bar("AAPL", 190.0)]
        quotes = await src.get_quotes(["AAPL"])
        assert quotes["AAPL"].source is SourceKind.MASSIVE_EOD


def test_classify_error():
    assert isinstance(classify_error(Exception("NOT_AUTHORIZED")), NotAuthorized)
    assert isinstance(classify_error(Exception("You are not entitled to this data")), NotAuthorized)
    other = RuntimeError("timeout")
    assert classify_error(other) is other
