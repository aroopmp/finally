# Massive API Reference (formerly Polygon.io)

How FinAlly retrieves real-time and end-of-day (EOD) stock prices for multiple tickers from Massive.

Sources: the Massive REST docs for [full market snapshot](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot), [grouped daily bars](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary), [previous day bar](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar), [last trade](https://massive.com/docs/rest/stocks/trades-quotes/last-trade) and the [quickstart](https://massive.com/docs/rest/quickstart), plus inspection of the `massive` Python SDK v2.8.0 models. Plan prices and the 5 calls/min free limit come from third-party summaries and are **unverified**; check massive.com/pricing before relying on them.

## 1. Basics

| Item | Value |
|---|---|
| Base URL | `https://api.massive.com` (legacy `api.polygon.io` still redirects) |
| Auth | `Authorization: Bearer <key>` header or `?apiKey=<key>` query param. The SDK sends the header. |
| SDK | `uv add massive` → `from massive import RESTClient` (synchronous; we run it in `asyncio.to_thread`) |
| Ticker symbols | Case-sensitive, upper case |

```python
from massive import RESTClient

client = RESTClient(api_key="...")   # or reads MASSIVE_API_KEY from the environment
```

## 2. Which endpoints work on which plan

This decides the whole design. Rows are the endpoints we care about.

| Endpoint | Basic (free) | Starter | Developer | Advanced |
|---|---|---|---|---|
| Full-market **snapshot** (`/v2/snapshot/.../tickers`) | **No** | Yes, 15-min delayed | Yes, 15-min delayed | Yes, real-time |
| **Grouped daily** bars (`/v2/aggs/grouped/.../{date}`) | Yes, EOD, 2y history | Yes, delayed | Yes, delayed | Yes, real-time |
| **Previous day** bar (`/v2/aggs/ticker/{t}/prev`) | Yes, EOD | Yes | Yes | Yes |
| **Last trade** (`/v2/last/trade/{t}`) | No | No | Yes, delayed | Yes, real-time |
| Trade data inside snapshot (`lastTrade`) | n/a | Only if the plan includes trades | Yes | Yes |

Consequences:

1. **A free key cannot give live prices.** The best it can do is the last close. The PLAN statement that REST polling "works on all tiers" is wrong for the snapshot endpoint.
2. **Starter/Developer data is 15 minutes old.** Quotes must be labelled delayed, not presented as live.
3. Paid-tier rate limits are reported as unlimited (stay well under ~100 req/s); the free tier is reported as 5 req/min. Unverified.

Expect HTTP 403 (`NOT_AUTHORIZED`) when the plan lacks an endpoint, 401 for a bad key, 429 for rate limiting. The Massive docs do not list these codes; confirm them against a real key. The SDK retries 5xx and 429 itself.

## 3. Real-time (or delayed) prices: full market snapshot

`GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,...`

One call returns every requested ticker, which is what keeps polling cheap. `tickers` is comma separated; an empty value returns the whole market. `include_otc=false` by default.

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key=KEY)
snaps = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,
    tickers=["AAPL", "MSFT", "NVDA"],
)
for s in snaps:
    print(s.ticker, s.last_trade.price, s.prev_day.close, s.todays_change_percent)
```

### Response shape (JSON → SDK attribute)

| JSON | SDK attribute | Notes |
|---|---|---|
| `ticker` | `ticker` | |
| `lastTrade.p` | `last_trade.price` | Present only if the plan includes trades |
| `lastTrade.t` | `last_trade.sip_timestamp` | **Nanoseconds** since epoch |
| `lastQuote.P/p/S/s/t` | `last_quote.*` | Only if the plan includes quotes; `t` in nanoseconds |
| `min.c`, `min.t` | `min.close`, `min.timestamp` | Latest minute bar; `t` in **milliseconds** |
| `day.o/h/l/c/v/vw` | `day.open/high/low/close/volume/vwap` | Today's bar so far |
| `prevDay.c` | `prev_day.close` | **Previous close, our daily-change baseline** |
| `todaysChange`, `todaysChangePerc` | `todays_change`, `todays_change_percent` | Versus previous close |
| `updated` | `updated` | Nanoseconds since epoch |

Gotchas that bit the first implementation:

- The SDK `LastTrade` model has **no `.timestamp`** attribute. It is `sip_timestamp` (SIP time), `participant_timestamp` (exchange time) and `trf_timestamp`. Reading `.timestamp` raises `AttributeError`.
- There is **no `day.previous_close`** in the model. Use `prev_day.close`.
- Timestamps are mixed units: trade/quote/`updated` are nanoseconds, `min.t` and aggregate `t` are milliseconds. Normalise explicitly (section 6).
- Snapshot data is cleared overnight (docs say around 3:30 AM ET; the SDK docstring says 12 AM) and repopulates from about 4:00 AM ET. Before then `last_trade`, `day` and `min` can be missing or zero. Fall back to `prev_day.close`.

### Choosing a price from a snapshot

Fields are optional depending on plan and time of day. Use this order and record which one won:

1. `last_trade.price`: true last sale (Developer and above)
2. `min.close`: latest minute bar
3. `day.close`: today's running bar, if non-zero
4. `prev_day.close`: stale; mark the quote `eod`

Reject any price that is `None`, non-finite or `<= 0`.

## 4. End-of-day prices for many tickers: grouped daily bars

`GET /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true`

A single call returns OHLCV for **every US stock** on one trading date. Filter locally to the tickers you care about. This is the only multi-ticker price call on the free plan, so it is the free-tier path.

```python
from datetime import date, timedelta

def eod_closes(client, tickers: set[str], on: date) -> dict[str, float]:
    bars = client.get_grouped_daily_aggs(on, adjusted=True)   # list[GroupedDailyAgg]
    return {b.ticker: b.close for b in bars if b.ticker in tickers and b.close}

def last_trading_day_closes(client, tickers, today: date, max_back: int = 7):
    """Walk back over weekends/holidays until the API returns bars."""
    d = today
    for _ in range(max_back):
        d -= timedelta(days=1)
        if d.weekday() >= 5:          # Sat/Sun never trade
            continue
        closes = eod_closes(client, tickers, d)
        if closes:                    # empty on holidays
            return d, closes
    raise LookupError("no trading day found")
```

Notes:

- Each result has `T` (ticker), `o h l c v vw n` and `t`, the **millisecond** timestamp for the aggregate window.
- Prices are split-adjusted by default (`adjusted=True`); keep that for consistency with `prevDay`.
- Today's date returns nothing on Basic until the day's data is published, so start from yesterday.
- The response is large (thousands of rows). Poll rarely: once at startup, then once per session day, not every few seconds.

### Single-ticker alternatives

```python
prev = client.get_previous_close_agg(ticker="AAPL")   # PreviousCloseAgg (or list; handle both)
```

`GET /v2/aggs/ticker/{t}/prev` returns the prior day's bar. It is one call per ticker, so it burns the free-tier budget quickly; prefer grouped daily for more than one or two tickers.

`get_last_trade(ticker)` (`/v2/last/trade/{t}`) returns `results.p` (price), `results.t` (SIP time, **nanoseconds**). It is Developer plan or above and one call per ticker, so use it only for a one-off check, not for polling.

## 5. How FinAlly uses Massive

| Situation | Behaviour |
|---|---|
| Key set, snapshot works, real-time plan | Poll snapshot every 2–5 s. Quotes tagged `massive-realtime`. |
| Key set, snapshot works, delayed plan | Poll every 5–15 s. Tagged `massive-delayed` (15 min old). |
| Key set, snapshot returns 403 (free plan) | **Fall back to EOD mode**: one grouped-daily call at startup and once a day. Prices are the last close, tagged `massive-eod`, and the UI must say so. No fake ticking. |
| Key set but 401 | Fail visibly: source status `error`, do not silently switch to the simulator. |
| Poll error after earlier success | Keep the last quote but let it age; the freshness policy marks it stale. |

Capability detection happens on the first poll: try the snapshot; on 403 switch to EOD mode and remember it for the process lifetime. Do not retry the snapshot every cycle.

The daily-change baseline in Massive mode is `prev_day.close` (snapshot) or the prior-session close from grouped daily.

## 6. Normalising timestamps

```python
import math

def to_unix_seconds(ts: int | float | None) -> float | None:
    """Massive timestamps are ns (trade/quote/updated) or ms (aggregates, min bar)."""
    if ts is None or not math.isfinite(ts) or ts <= 0:
        return None
    if ts >= 1e17:          # ~2001 in ns is 1e18; 1e17 ns is 1973
        return ts / 1e9     # nanoseconds
    if ts >= 1e11:          # ms (1e11 ms is 1973)
        return ts / 1e3
    return float(ts)        # already seconds
```

The magnitude check is a guard, not a substitute for knowing each field's unit. Prefer calling it with the unit-specific fields from section 3, and unit-test it against a fixture deserialised by the real SDK model (not `MagicMock`).

## 7. Testing against Massive

- Build fixtures by feeding sample JSON through `TickerSnapshot.from_dict` and `GroupedDailyAgg.from_dict` so attribute names are checked by the SDK.
- Cover: missing `lastTrade`, zero `day`, missing `prevDay`, ticker absent from the response, 403, 401, 429, empty grouped-daily result (holiday).
- Live smoke test (manual, needs a key, skipped in CI): fetch 3 tickers from snapshot and grouped daily; assert timestamps convert to a plausible recent date.

## 8. Open items

- Confirm with a real free key that the snapshot returns 403 and which JSON `status`/`error` it carries.
- Confirm the exact overnight reset time of snapshot data.
- Decide whether a Starter plan (no trades) is supported via `min.close`/`day.close`, or only Developer and above.
