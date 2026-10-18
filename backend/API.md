# FinAlly REST and SSE contract

All errors: `{"error": {"code": str, "message": str}}` with 400 (bad input, insufficient cash/shares,
unknown ticker), 404 (not on watchlist), 503 (`quote_unavailable`). Times are ISO-8601 strings in
REST bodies and Unix seconds (float) in SSE. Money is JSON numbers rounded to cents.

## Trades (`POST /api/portfolio/trade`)
Body `{ticker, quantity, side}`. Executes inside one `BEGIN IMMEDIATE` SQLite transaction (cash,
position, trade row, snapshot commit together; any failure rolls back). Mutations are serialised by
`state.lock`. The LLM call in chat happens outside the lock/transaction.

- Fill price: a fresh cached quote; an untracked ticker is fetched first. No fresh quote -> 503,
  never a zero fill. Cash is rounded half-up to cents; affordability uses the rounded amount.
- Quantity: finite, > 0, <= 4 decimals, minimum 0.01. Exception: a sell of the entire holding may
  be below 0.01, so fractional remainders are always liquidatable. Positions < 0.0001 are deleted.
- Response: `{trade: {id, ticker, side, quantity, price, total, executed_at}, cash_balance, position|null}`.

## Valuation
`GET /api/portfolio` values a holding without a fresh quote at cost basis (`price: null`,
`stale: true`, `prices_complete: false`); it never becomes 0. Snapshots (background and post-trade)
are skipped while any holding lacks a fresh price.

## Reset (`POST /api/reset`)
Runs under `state.lock`, bumps `state.epoch`, restores cash/watchlist, inserts a fresh $10,000
snapshot, restarts the market source. A chat request in flight at reset finds a changed epoch after
its LLM call returns and is discarded (nothing executed or stored).

## SSE (`GET /api/stream/prices`)
`retry: 1000` first. Then, whenever the cache changes (~500 ms cadence), one `data:` event holding a
full snapshot keyed by ticker (an empty `{}` means nothing is tracked; a new connection immediately
gets the current snapshot):

    {"AAPL": {"ticker": "AAPL", "price": 190.5, "prev_price": 190.4, "prev_close": 189.0,
              "change": 0.1, "direction": "up|down|flat", "day_change": 1.5,
              "day_change_pct": 0.79, "as_of": 1760000000.123, "source": "simulator"}}

Clients should append to chart history only when `as_of` advances for a ticker.
