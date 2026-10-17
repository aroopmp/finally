# Market Simulator

How FinAlly simulates stock prices when `MASSIVE_API_KEY` is not set. It is the default, runs in-process with no external dependencies, and implements the same `MarketDataSource` interface as the Massive client ([MARKET_INTERFACE.md](MARKET_INTERFACE.md)). The code already exists in `backend/app/market/simulator.py` and `seed_prices.py`; this document records the approach, the code structure and the changes the interface revision requires.

## 1. Requirements (from PLAN.md §6)

- Geometric Brownian motion (GBM) with configurable drift and volatility per ticker.
- Update about every 500 ms.
- Correlated moves across tickers (tech together).
- Occasional random events: sudden 2–5% moves for drama.
- Realistic seed prices (AAPL ~$190, and so on).
- Background asyncio task writing to the shared `PriceCache`.

## 2. The model

Each ticker follows

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z )
```

- `μ` annualised drift, `σ` annualised volatility, both per ticker.
- `dt` is one tick as a fraction of a trading year: `0.5 s / (252 · 6.5 · 3600 s) ≈ 8.5e-8`. Using trading seconds, not calendar seconds, makes `σ` match the quoted annual volatility figures. Per tick moves are tiny (a few cents on a $200 stock) and accumulate over minutes, which looks like a real tape.
- `exp()` form guarantees prices stay positive and the log-return is exactly normal.
- `Z` is a **correlated** standard normal draw (next section).

Rough behaviour: for σ = 0.25, one tick has a standard deviation of `0.25·√(8.5e-8) ≈ 7.3e-5`, about 1.4 cents on $190. Over one trading day (46,800 ticks at 500 ms) the standard deviation is about 1.6%, a plausible daily range.

## 3. Correlated moves

Independent draws `z ~ N(0, I)` become correlated draws `Z = L·z`, where `L` is the Cholesky factor of the correlation matrix `C = L·Lᵀ`.

Pairwise correlations (from `seed_prices.py`):

| Pair | ρ |
|---|---|
| Both in the tech group (AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX) | 0.6 |
| Both in finance (JPM, V) | 0.5 |
| Involving TSLA | 0.3 (does its own thing) |
| Cross-sector or unknown ticker | 0.3 |

All values are positive and below 1 with a unit diagonal, so `C` is positive definite and `numpy.linalg.cholesky` succeeds for any ticker mix. The factor is rebuilt (O(n²), n < 50) whenever a ticker is added or removed; `n ≤ 1` skips it.

## 4. Random events

On each tick, each ticker independently has `event_probability = 0.001` of a shock: a uniform 2–5% jump, random sign, applied after the GBM step. With 10 tickers at 2 ticks/s that is an event roughly every 50 seconds somewhere on the board, enough drama without making the market feel broken. Events are not correlated across tickers.

## 5. Code structure

```
simulator.py
├── GBMSimulator            pure math, synchronous, no I/O, easy to unit test
│     __init__(tickers, dt, event_probability)
│     step() -> {ticker: price}          advance all tickers one tick
│     add_ticker / remove_ticker         rebuild Cholesky
│     get_price / get_tickers
│     _pairwise_correlation(t1, t2)      staticmethod
└── SimulatorDataSource     MarketDataSource implementation
      start / stop / add_ticker / remove_ticker / get_tickers
      get_quotes / get_eod_closes        (added by the interface revision)
      _run_loop()                        step → cache.update → sleep(update_interval)

seed_prices.py
      SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS, CORRELATION_GROUPS, *_CORR constants
```

Key design points:

- **Math separated from scheduling.** `GBMSimulator` knows nothing about asyncio or the cache. Tests call `step()` directly with a seeded RNG.
- **Seeded start.** `start()` writes the seed prices to the cache before launching the loop so the first SSE event is full and the watchlist is never blank.
- **Failure isolation.** The loop catches exceptions per step, logs, and carries on. A bug in one tick must not kill the feed.
- **Prices rounded to cents** when written to the cache (`round(price, 2)`), while the internal state keeps full precision so tiny moves are not lost to rounding.
- **Dynamic tickers.** `add_ticker` uses `SEED_PRICES[ticker]` if known, otherwise a random price in $50–$300 and default params (`σ = 0.25`, `μ = 0.05`). Whether unknown tickers should be accepted at all is an open product decision (MARKET_INTERFACE.md §9).

## 6. Changes required by the interface revision

The simulator works today; these edits align it with the new contract:

1. **Daily baseline.** On `start()` and `add_ticker()` record the seed (or first) price as `prev_close` and pass it to every `cache.update(...)`. The UI labels this "change since start" in simulator mode. Previously the cache discarded it and `change_percent` was only per tick.
2. **Source tag and times.** Pass `source=SourceKind.SIMULATOR` and `as_of=time.time()`; set `kind = SourceKind.SIMULATOR` and `status = OK` after a successful start.
3. **One-shot methods.**
   - `get_quotes(tickers)` returns the current cache quotes, creating simulator state on demand if unknown tickers are allowed.
   - `get_eod_closes(tickers, on=None)` returns the recorded `prev_close` (the seed). It is not historical data, and the docstring says so.
4. **`add_ticker` contract.** The cache entry already exists on return (it does today).
5. **Time source.** Use `time.time()` (wall clock) for `as_of`, injected as a callable so freshness tests can control it.
6. **Reset.** `POST /api/reset` decides whether to restart the simulator from seeds. Recommended: restart, so a reset returns the market to a known state, and `prev_close` is reset with it.

## 7. Tuning knobs

| Knob | Default | Effect |
|---|---|---|
| `update_interval` | 0.5 s | Tick rate; `dt` must scale with it (`dt = interval / trading_seconds_per_year`) |
| `event_probability` | 0.001 | Shock frequency; set to 0 in tests |
| `TICKER_PARAMS[t]["sigma"]` | 0.17–0.50 | Volatility (TSLA 0.50, V 0.17) |
| `TICKER_PARAMS[t]["mu"]` | 0.03–0.08 | Drift; irrelevant at tick scale, matters only for long runs |
| correlation constants | 0.3 / 0.5 / 0.6 | Co-movement strength |

If `update_interval` is changed, `dt` currently stays at the 500 ms value unless passed in; compute it from the interval so volatility stays right.

For E2E tests needing exact numbers, add an optional deterministic mode: `SIM_SEED=<int>` seeds both `numpy.random` and `random`, and `event_probability=0` removes shocks. Exact cash/P&L assertions should still pin the quote through a test hook rather than rely on the walk.

## 8. Tests

Existing suite (`backend/tests/market/test_simulator.py`, `test_simulator_source.py`): prices stay positive; GBM statistics; correlation of tech pairs above cross-sector pairs; add/remove rebuild; cache seeded at start; loop writes updates.

Add:

- `prev_close` set at start and unchanged by later ticks; `day_change_pct` and `direction` computed from different baselines.
- Deterministic mode: the same `SIM_SEED` yields the same sequence of 100 steps.
- Statistical check: over many steps with events off, log-return standard deviation within 5% of `σ·√dt` (generous tolerance, fixed seed).
- Cholesky never fails for 1, 2, 10 and 50 tickers, including all unknown tickers.
- A shock multiplies the price by a factor within [0.95, 0.98] ∪ [1.02, 1.05] (force `event_probability=1`).
- The loop survives an exception thrown inside `step()` and keeps ticking.
- `get_eod_closes` returns the baseline, and unknown tickers follow the policy chosen in MARKET_INTERFACE.md §9.

## 9. Known limitations

- No market hours, gaps or halts; it ticks 24/7 at constant volatility.
- No volume, bid/ask or intraday seasonality.
- Prices drift away from the real world over time because the seeds are fixed.
- Process restart resets prices to the seeds, so a portfolio opened earlier will show P&L against different prices. This is acceptable for a demo and should be stated in the UI help.
