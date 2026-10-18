---
last_mapped_commit: d527b54d508a1743d51a2c3cd9465cee798b6701
last_mapped_at: 2026-10-18
---
# Codebase Concerns

**Analysis Date:** 2026-10-18

## Scope Notes

- The `frontend/` directory is deleted in the working tree but tracked in git HEAD. Frontend concerns below are based on the tracked files (`git show HEAD:frontend/...`) and were not re-verified against a working copy.
- `planning/REVIEW.md` (2026-10-16) lists open contract-level findings. Items that match code are cross-referenced as "REVIEW #n".

## Tech Debt

**Chat persistence is split from action execution:**
- Issue: `handle_chat` executes each LLM action in its own transaction via `_run_actions`, then writes the user and assistant rows to `chat_messages` in a separate `transaction(...)`. A crash or DB error between the two leaves trades executed with no chat audit record.
- Files: `backend/app/chat.py` (`handle_chat`, `_run_actions`)
- Impact: Audit gap; trades exist without the explanation that produced them; retry may re-execute actions the user already saw.
- Fix approach: Commit each action's mutation together with its result record (REVIEW #5), or persist a pending chat exchange with a request ID before executing actions and mark it complete afterward.

**Trade execution and chat exchange are not idempotent:**
- Issue: `POST /api/portfolio/trade` and `POST /api/chat` carry no client request ID. A retried request after a lost response executes a second trade.
- Files: `backend/app/routes.py` (`post_trade`, `post_chat`), `backend/app/services/trading.py` (`execute_trade`)
- Impact: Duplicate fills and double cash deduction on network retry.
- Fix approach: Add a client-generated `request_id` column (unique) on `trades` and `chat_messages`; replay stored result for an identical ID+payload; reject mismatched payloads (REVIEW #5).

**Stream payload contract is undocumented and contradicts the plan:**
- Issue: `backend/app/market/stream.py` docstring and code emit a map of all tickers (`{"AAPL": {...}, ...}`) with Unix-second timestamps, while `planning/PLAN.md` section 6 describes per-ticker events with millisecond-style fields. `PriceUpdate.change_percent` is change since the previous tick, not the session baseline required by the plan.
- Files: `backend/app/market/stream.py`, `backend/app/market/models.py`, `backend/app/market/cache.py`, `planning/PLAN.md`
- Impact: Frontend built against the plan will not integrate; daily change % is wrong for the watchlist.
- Fix approach: Publish one schema (REVIEW #4); keep tick direction for flashing separate from session change; store baseline (first simulator price / previous close) in the cache.

**Stream suppresses empty snapshots and removals do not bump version:**
- Issue: `PriceCache.remove` and `clear` bump `_version`, but the stream generator skips empty snapshots, so clients never learn that a ticker was removed when the cache empties.
- Files: `backend/app/market/cache.py`, `backend/app/market/stream.py`
- Impact: Stale tickers can remain on the client after removal; sparkline/chart history grows without bound on the client.
- Fix approach: Send an explicit empty/removal event; bound client-side history per ticker.

**Dead or duplicate schema seed path risk:**
- Issue: `init_db` runs `create_schema` and `seed_defaults` on every startup inside `BEGIN IMMEDIATE`; seed idempotence depends on `seed_defaults` checks rather than explicit schema versioning.
- Files: `backend/app/db.py`, `backend/app/schema/seed.py`, `backend/app/schema/schema.sql`
- Impact: Any future schema change requires manual handling; no migration path for existing `db/finally.db` volumes.
- Fix approach: Add a `schema_version` row and explicit migrations before the first schema change.

**Market data stub artifacts in the backend tree:**
- Issue: `backend/market_data_demo.py` and `backend/app/__pycache__/` are present alongside the app package.
- Files: `backend/market_data_demo.py`
- Impact: Confusing entry points; demo script may drift from the real factory.
- Fix approach: Move the demo under `backend/scripts/` or delete it once the market data work is signed off.

## Known Bugs and Correctness Risks

**Fractional remainders can become unsellable:**
- Symptoms: Buying `0.0101` shares then selling `0.01` leaves a `0.0001` position that cannot be sold because `MIN_QUANTITY` is `0.01`. Any remainder between `0.0001` and `0.01` has the same problem.
- Files: `backend/app/services/trading.py` (`_apply_trade`, `MIN_QUANTITY`, `DUST`)
- Trigger: Partial sell leaving a sub-0.01 remainder.
- Workaround: None in the UI; the sell branch only exempts exact full-position liquidation (`qty != held`), which does not help a remainder below 0.01.
- Fix approach: Allow liquidation of any remainder not exceeding the held quantity, or make the rule explicit (REVIEW #6).

**Quotes for untracked tickers are fetched during the trade:**
- Symptoms: `_fill_quote` calls `state.source.add_ticker` for a ticker without a fresh cached quote. In Massive mode this only schedules the ticker for the next poll and may return `503 quote_unavailable`.
- Files: `backend/app/services/trading.py` (`_fill_quote`), `backend/app/market/massive_client.py` (`add_ticker`)
- Trigger: Trading a supported ticker that is not in the watchlist or positions, or after a failed poll.
- Workaround: Add the ticker to the watchlist first.
- Fix approach: Define a quote-readiness policy and freshness window (REVIEW #2).

**Massive timestamp units may be wrong:**
- Symptoms: `massive_client.py` converts timestamps via `to_unix_seconds`, but REVIEW #3 states the SDK's last-trade timestamp is nanoseconds. Mocked tests use millisecond values and cannot detect the mismatch.
- Files: `backend/app/market/massive_client.py` (lines ~73–82), `backend/tests/market/test_massive.py`
- Trigger: Real Massive mode with trade entitlement.
- Fix approach: Add a fixture deserialized by the locked SDK version and normalize nanoseconds to seconds (divide by 1e9).

**Massive full-market snapshot endpoint may be unavailable on common tiers:**
- Symptoms: The adapter uses the full-market snapshot endpoint, which REVIEW #3 reports is excluded from Stocks Basic. Plans may be delayed, and `lastTrade` requires entitlement.
- Files: `backend/app/market/massive_client.py` (`_fetch_snapshots`, `_quotes_from_snapshots`), `planning/MASSIVE_API.md`
- Trigger: Setting `MASSIVE_API_KEY` on a free/basic plan.
- Fix approach: Document supported plans; surface entitlement errors visibly (the `classify_error` path maps `AuthError`).

**Chat LLM call is blocking inside a worker thread but holds no lock:**
- Symptoms: `call_llm` runs via `asyncio.to_thread`; the epoch check at `handle_chat` prevents a late response from trading after reset (409 `account_reset`). This is correct, but the user message is not stored for a 409 or LLM failure, so the user's text is lost from history.
- Files: `backend/app/chat.py` (`handle_chat`)
- Trigger: Reset while a chat request is in flight, or LLM failure.
- Fix approach: Acceptable per plan ("failed exchange is not stored"); document it in the frontend so users re-send.

## Security Considerations

**API has no authentication and exposes mutation endpoints:**
- Risk: `POST /api/reset`, `POST /api/portfolio/trade`, `POST/DELETE /api/watchlist`, and `POST /api/chat` (which spends the OpenRouter key) are unauthenticated.
- Files: `backend/app/routes.py`, `backend/app/main.py`
- Current mitigation: The start script publishes only on loopback (`scripts/start_mac.sh`: `-p "127.0.0.1:$PORT:8000"`). The container itself binds `0.0.0.0` (`Dockerfile` CMD), which is fine only while the host publish stays on loopback.
- Recommendations: Keep the loopback publish in any documented command (REVIEW #11); add a note in `README.md` that `-p 8000:8000` exposes the app to the LAN. Consider a CORS policy is unnecessary (same origin) but should not be widened.

**Secrets handling and `.env` loading:**
- Risk: `config.load_dotenv` loads the project-root `.env` for local runs and uses `setdefault`; no secret is logged directly, but `handle_chat` logs exception text (`logger.warning("LLM call failed: %s", exc)`) which may include provider response details.
- Files: `backend/app/config.py`, `backend/app/chat.py`
- Current mitigation: `.env` is gitignored; `.env.example` is referenced but not present in the tracked tree (`git ls-tree` shows only `.env.example` at root in some checkouts; verify before relying on it).
- Recommendations: Redact provider error bodies before logging; confirm `.env.example` is committed.

**Unbounded LLM context and prompt injection via tool actions:**
- Risk: The LLM output directly drives `execute_trade` and watchlist changes. Injected instructions in market text or chat history can cause trades. Validation limits (cash, shares) bound the damage but not the trade count.
- Files: `backend/app/chat.py` (`_run_actions`, `build_context`)
- Current mitigation: Per-action validation via shared `execute_trade`; actions run in order and failures do not stop later ones.
- Recommendations: Cap the number of actions per response and per minute; treat the LLM output as untrusted (already parsed with a schema).

**Ticker input validation depends on a single parser:**
- Risk: Tickers are interpolated only through parameterized SQL, so no injection is observed. The `DELETE /api/watchlist/{ticker}` path still relies on `parse_ticker`.
- Files: `backend/app/services/watchlist.py` (`parse_ticker`), `backend/app/routes.py`
- Current mitigation: Parameterized queries throughout `backend/app/services/*.py`.
- Recommendations: Keep all SQL parameterized; add a test for lowercase and invalid symbols on DELETE.

## Performance Bottlenecks

**Global asyncio lock serializes all mutations and snapshots:**
- Problem: `state.lock` (`backend/app/state.py`) guards trades, watchlist edits, reset, snapshots, and the post-LLM chat write. The 30-second snapshot loop and any trade wait on it.
- Files: `backend/app/state.py`, `backend/app/routes.py`, `backend/app/main.py` (`_snapshot_loop`), `backend/app/chat.py`
- Cause: Single lock for a single-user app; `execute_trade` also calls `reconcile_tracked` in `finally`, which may await the market source while the lock is held.
- Improvement path: Keep the lock scope minimal; move `reconcile_tracked` outside the lock where it does not affect the trade result.

**Per-operation SQLite connections:**
- Problem: `backend/app/db.py` opens a new connection and re-runs `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` for every operation.
- Files: `backend/app/db.py`
- Cause: One short-lived connection per operation by design.
- Improvement path: Acceptable at current volume; if the 500ms stream reads the DB, move price reads to the in-memory cache only.

**Snapshot retention enforced by row count on every insert:**
- Problem: `trim_snapshots` deletes beyond 20,000 rows; without an index on `recorded_at` ordering this can scan.
- Files: `backend/app/services/portfolio.py` (`trim_snapshots`), `backend/app/schema/schema.sql`
- Improvement path: Confirm `portfolio_snapshots` has an index on `recorded_at`; trim less often.

**Portfolio history endpoint default limit:**
- Problem: `GET /api/portfolio/history` defaults to `limit=2000` and allows up to 20000 rows in one JSON response.
- Files: `backend/app/routes.py` (`get_history`)
- Improvement path: Downsample for the P&L chart on the server.

## Fragile Areas

**Market source selection and both implementations:**
- Files: `backend/app/market/factory.py`, `backend/app/market/simulator.py`, `backend/app/market/massive_client.py`, `backend/app/market/interface.py`
- Why fragile: Two implementations share an interface, but the simulator accepts unknown symbols with random seed prices (`planning/REVIEW.md` #7) while the plan rejects them in both modes. Downstream code (`trading.py`, `watchlist.py`) relies on `UnknownTicker` and `QuoteUnavailable` being raised consistently.
- Safe modification: Change validation through one shared validator and update `backend/tests/market/test_simulator_source.py` together.
- Test coverage: Simulator tests assert the current permissive behavior.

**Lifecycle ordering and reset:**
- Files: `backend/app/main.py` (`lifespan`), `backend/app/services/lifecycle.py`, `backend/app/state.py` (`epoch`)
- Why fragile: Startup runs `init_db`, then `source.start(tracked_set)`, then starts the snapshot task. Reset must reconcile the tracked set and the simulator state; REVIEW #8 notes that reset does not define simulator restart and that held symbols absent from the watchlist must remain priced.
- Safe modification: Keep the sequence in `lifespan`; test restart with held-but-unwatched symbols.

**Trade service as the single mutation path:**
- Files: `backend/app/services/trading.py`
- Why fragile: `_apply_trade` mixes validation, average-cost math, dust deletion, trade logging, and snapshot insertion in one transaction. Decimal-to-float conversion occurs before storage (`float(new_cash)`), so REAL storage can drift from the Decimal rules.
- Safe modification: Add tests for exact liquidation, sub-cent amounts, and boundary quantities before changing rounding.
- Test coverage: `backend/tests/test_api.py` and `backend/tests/test_chat.py` cover common paths; concurrent overspend and mid-write rollback are not covered.

**Frontend deleted from the working tree:**
- Files: `frontend/` (tracked at HEAD: `frontend/components/*.tsx`, `frontend/hooks/usePriceStream.ts`, `frontend/__tests__/*`)
- Why fragile: The Dockerfile's frontend stage (`COPY frontend/`) cannot build from the current working tree, and the `frontend/.gitignore` deletion is also pending.
- Safe modification: Restore the frontend directory before running `docker build` or the E2E suite.

## Scaling Limits

**Single process and in-memory price cache:**
- Current capacity: One uvicorn worker; `PriceCache` is a process-local dict guarded by `threading.Lock`.
- Limit: Multiple workers or replicas would each run their own source and cache, causing divergent prices and duplicate snapshot loops.
- Scaling path: Keep `uvicorn` at one worker (the Dockerfile CMD does this); document the single-replica constraint from `planning/PLAN.md` section 11.

**SSE connections per client:**
- Current capacity: Each `/api/stream/prices` client holds an open generator polling the cache about every 500ms.
- Limit: Many open tabs multiply work and hold connections open.
- Scaling path: Fine for single-user local use; cap concurrent SSE streams if exposed.

## Dependencies at Risk

**Massive SDK pinned against documented behavior:**
- Package: `massive` (Python SDK, imported in `backend/app/market/massive_client.py`)
- Risk: The adapter relies on attribute names and timestamp units (`sip_timestamp`, `timestamp`) that are not validated by real SDK fixtures (REVIEW #3).
- Impact: Incorrect chart dates and freshness checks; `AuthError` mapping may miss other error classes.
- Migration plan: Add SDK-deserialized fixtures; pin the version in `backend/uv.lock` and document it.

**LiteLLM and OpenRouter/Cerebras routing:**
- Package: `litellm` via `backend/app/chat.py` (`call_llm`)
- Risk: Provider model naming (`openrouter/openai/gpt-oss-120b`) and structured-output support can change; errors are caught broadly (`except Exception`).
- Impact: Chat returns a generic `llm_error` for all provider failures, hiding causes during development.
- Migration plan: Keep the model name in one constant; add a contract test with the mock path and a recorded provider response.

## Missing Critical Features

**Request idempotency and audit records:**
- Problem: No request IDs on trades or chat (see Tech Debt).
- Blocks: Safe retries from the frontend and reliable audit of LLM-driven trades.

**Explicit quote freshness and readiness state:**
- Problem: The API does not expose whether a price is live, delayed, or stale; `PriceUpdate` has no availability field.
- Blocks: Trusted portfolio valuation during provider outages and closed markets (REVIEW #2).

**Frontend refresh after mutations:**
- Problem: SSE carries prices only; cash, positions, watchlist membership, and history must be refetched or returned by mutation responses after trades, chat actions, and reset (REVIEW #9).
- Blocks: Header and positions updating after a chat-initiated trade without reload.

## Test Coverage Gaps

**Concurrent trade and chat behavior:**
- What's not tested: Concurrent manual and chat trades overspending or overselling; mid-write rollback.
- Files: `backend/tests/test_api.py`, `backend/tests/test_chat.py`, `backend/app/services/trading.py`
- Risk: The check-then-write path is protected by `BEGIN IMMEDIATE` and `state.lock`, but no test exercises it under concurrency.
- Priority: High

**Massive response contract:**
- What's not tested: Real SDK deserialization, timestamp units, and entitlement errors.
- Files: `backend/tests/market/test_massive.py`
- Risk: Mocked `MagicMock` attributes pass while the real SDK response breaks charts and freshness.
- Priority: High

**Stream contract and reconnect:**
- What's not tested: Payload shape, initial snapshot on reconnect, removal behavior, and empty-cache events.
- Files: `backend/tests/market/test_stream.py`, `backend/app/market/stream.py`
- Risk: Frontend integration breaks silently.
- Priority: High

**Reset during in-flight chat and restart with held-but-unwatched tickers:**
- What's not tested: The epoch check path (409) and lifecycle restart with positions outside the watchlist.
- Files: `backend/app/chat.py`, `backend/app/services/lifecycle.py`, `backend/app/main.py`
- Risk: Stale trades or unpriced holdings after reset or restart.
- Priority: Medium

**E2E isolation:**
- What's not tested: Parallel E2E runs share one database volume and nondeterministic simulator prices.
- Files: `test/tests/app.spec.ts`, `test/playwright.config.ts`, `scripts/start_mac.sh`
- Risk: Flaky cash, P&L, and color assertions; cross-test state leakage.
- Priority: Medium

---

*Concerns audit: 2026-10-18*
