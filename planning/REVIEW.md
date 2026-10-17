**Review of FinAlly's implementation plan — 2026-10-16**

The single-container architecture and simulated, single-user scope are appropriate for this project. The shared trade service, tracked-ticker union, mock chat mode, and separation of essential UI from polish are useful decisions. The plan needs another contract pass before the remaining components can be implemented independently: several observable behaviors are undefined, and the existing market-data implementation does not satisfy all of the revised requirements.

This review covers the current working-tree [PLAN.md](PLAN.md), the market-data code, and its summary/tests. P1 means resolve before building the dependent feature; P2 means resolve before acceptance. This was a documentation and code inspection, not an application test run.

1. **P1 — Define an atomic trade transaction.**

   References: [database rules](PLAN.md#7-database), lines 200–230; [trade endpoint](PLAN.md#8-api-endpoints), line 258.

   Sharing `execute_trade()` does not guarantee that checking cash/holdings and changing them is atomic. A manual request and a chat request can overlap even with one user and one server process. Both could validate against the same balance, or a failed write could leave cash, holdings, and trade history inconsistent.

   Require a SQLite write transaction that acquires write ownership before reading the account/position, validates against that state, and commits the cash change, position change, trade record, and required post-trade snapshot together. Capture one eligible fill quote for the execution. Keep LLM/provider network calls outside the transaction. Add concurrent overspending/overselling and mid-write rollback tests. Section 13 already identifies atomicity as open; this needs an explicit contract in sections 7–8.

2. **P1 — Specify when a price is eligible for trading and valuation.**

   References: [market data](PLAN.md#6-market-data), lines 162–181; [snapshots](PLAN.md#7-database), line 227; [trade endpoint](PLAN.md#8-api-endpoints), line 258.

   The tracked set is watchlist ∪ positions, but the trade bar accepts arbitrary supported tickers. A supported ticker removed from the watchlist and not yet held has no cached quote. In Massive mode, [adding a ticker](../backend/app/market/massive_client.py) only schedules it for the next poll; poll failures retain old quotes. Neither case is equivalent to an invalid symbol.

   Define how a first trade obtains a quote, the timeout/not-ready response, and an explicit freshness policy for simulator, delayed provider data, closed markets, and provider outages. Distinguish quote time from last successful fetch time. Require positive, finite prices and prevent missing holdings prices from silently becoming zero in portfolio totals or persisted snapshots. Expose availability/staleness separately from SSE connection status. Test startup without quotes, a supported untracked ticker, and a failed poll after a successful one.

3. **P1 — Revalidate Massive compatibility before treating market data as finished.**

   References: [Massive API](PLAN.md#massive-api-optional), lines 159–165; [build order](PLAN.md#build-order), line 479.

   The statement that REST polling works on all tiers is incompatible with the endpoint used by [the existing adapter](../backend/app/market/massive_client.py). Its full-market snapshot endpoint is excluded from Stocks Basic, other plans can be delayed, and `lastTrade` is conditional on trade entitlement. Increasing the polling interval cannot supply missing endpoint access or fields. Document the supported subscription/data behavior and handle unavailable entitlements visibly, or choose another explicitly supported data path. [Massive snapshot documentation](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot).

   There is also a timestamp compatibility issue to resolve: the adapter assumes `last_trade.timestamp` is milliseconds and divides by 1,000, while Massive documents the trade timestamp as nanoseconds. The current [tests](../backend/tests/market/test_massive.py) manufacture that attribute and millisecond values with `MagicMock`, so they cannot validate the actual SDK response contract. Add a fixture deserialized by the locked SDK, verify its timestamp attribute, and normalize nanoseconds to Unix seconds with division by 1,000,000,000. Otherwise chart dates and freshness calculations can be invalid even when mocked tests pass. [Massive last-trade documentation](https://massive.com/docs/rest/stocks/trades-quotes/last-trade).

4. **P1 — Publish the actual SSE schema and add the missing daily-change data.**

   References: [SSE streaming](PLAN.md#sse-streaming), lines 176–182; [frontend](PLAN.md#10-frontend-design), lines 357–369.

   The plan describes each event as containing a ticker/price, but [stream.py](../backend/app/market/stream.py) sends a map of all cached tickers, such as `{"AAPL": {...}, "MSFT": {...}}`, only when the cache version changes. Its timestamps are numeric Unix seconds. A frontend built against a single-ticker event or millisecond timestamps will not integrate correctly.

   Specify an example payload, timestamp units, full-snapshot versus delta semantics, removal/empty-set behavior, and the initial snapshot on reconnect. Define how clients avoid adding repeated quote timestamps to charts and bound the in-memory chart history. The current cache does not advance its version on removal, and the stream suppresses empty snapshots; account for this if clients rely on snapshots to remove symbols.

   Separately, [PriceUpdate.change_percent](../backend/app/market/models.py) measures change since the previous update, not the session-start/previous-close baseline required at line 180. The cache discards that baseline. Preserve tick direction for flashing and introduce explicit baseline/session-change fields for the watchlist. Label the simulator metric as change since simulation start. Add fixtures that distinguish these two percentages and test reconnect/removal behavior against the real stream generator.

5. **P2 — Make manual and chat retries safe.**

   References: [mutation endpoints](PLAN.md#8-api-endpoints), lines 258 and 272; [chat flow](PLAN.md#how-it-works), lines 292–299.

   If execution succeeds but the response is lost, the proposed contract provides no request identity to distinguish a retry from a new trade. Retrying the same POST can therefore execute it again. The proposed chat flow also executes actions before saving its exchange, leaving an audit gap if persistence fails afterward.

   Add a client-generated request ID for trade/chat requests and persist execution outcomes. Replaying the same ID and payload should return the existing result; reusing an ID with a different payload should fail. For multi-action chat, retain stable action indices and commit each action's result with its mutation so a retry cannot replay completed trades. Preserve the plan's continue-after-validation-failure behavior. Verify a lost response followed by a retry produces one trade and one cash change.

6. **P2 — Resolve the fractional-share rule that leaves unsellable balances.**

   Reference: [money rules](PLAN.md#7-database), line 200.

   Buying `0.0101` shares and selling `0.01` leaves `0.0001`. That remainder is retained because deletion applies only below `0.0001`, but it cannot be sold because the minimum order is `0.01`. Larger remainders below `0.01` have the same problem.

   Permit full-position liquidation below the normal minimum, or choose a compatible quantity increment. Define decimal quantization and the cash rounding mode, including which rounded amount is used for affordability checks and average-cost accounting. `REAL` storage alone does not define those rules. Test exact liquidation, the example above, and cash/quantity boundary values; reject non-finite numeric inputs.

7. **P2 — Choose one supported ticker universe and make the examples match it.**

   References: [ticker validation](PLAN.md#sse-streaming), line 179; [chat example](PLAN.md#structured-output-schema), line 312.

   Rejecting every ticker without a simulator seed in both modes restricts the app to the ten symbols in [SEED_PRICES](../backend/app/market/seed_prices.py), all of which are already on the initial watchlist. Consequently, adding the example `PYPL` must fail. Meanwhile, [the simulator](../backend/app/market/simulator.py) currently accepts unknown symbols with random seed prices, and its tests explicitly expect that behavior.

   Either make the ten-symbol allowlist an explicit product restriction and replace the example, or add a defined catalog beyond the default watchlist. State whether real mode has the same catalog. Enforce the choice through one shared validator used by manual and chat actions, and align the existing simulator tests. Include a successful non-default addition if that remains an intended feature.

8. **P2 — Define startup and reset as coordinated lifecycle operations.**

   References: [lazy initialization](PLAN.md#key-boundaries), line 111; [database initialization](PLAN.md#sqlite-with-lazy-initialization), line 190; [reset](PLAN.md#8-api-endpoints), line 259.

   The plan alternates between first-request initialization and startup initialization even though market tracking and snapshots need persisted state before they run. Specify one sequence: initialize/seed idempotently, load watchlist ∪ positions, start the source, then enable valuation/snapshot work under the quote-readiness policy. Define readiness independently of simple process health.

   Reset needs more than deleting rows. An LLM request started before reset can return afterward, trade against the fresh account, and repopulate cleared chat history. Serialize reset with mutations and reject late results from the previous account generation, without holding a database transaction over the LLM call. Reconcile tracked symbols, insert a fresh $10,000 baseline snapshot, and define whether simulator state is restarted. Test a delayed chat response across reset and a restart with held symbols absent from the watchlist.

9. **P2 — Complete the REST and chat result contracts before frontend work.**

   References: [API endpoints](PLAN.md#8-api-endpoints), lines 247–278; [structured output and execution](PLAN.md#structured-output-schema), lines 303–330; [frontend](PLAN.md#10-frontend-design), lines 360–364.

   Endpoint descriptions and a model-output example do not define frontend response types. Document request/response examples, errors/status codes, number/time formats, history ordering/limits, and the action-result structure. Include trade IDs, actual fill prices, executed quantities, and per-action failures. Preserve the existing decision that server action results determine success; include those results in later LLM context so failed executions do not become apparent successes through assistant prose alone.

   The separate `trades` and `watchlist_changes` arrays also do not express one cross-type order despite the promise that actions execute in order. Specify a fixed order or use one typed action array. Validate the complete model response before any side effects, then apply business validation per action.

   Finally, specify frontend refresh behavior after manual/chat mutations and reset. Prices-only SSE cannot update cash, quantities, watchlist membership, or the snapshot-history query. Choose mutation responses or refetches for those changes and a refresh cadence for the P&L chart. Test a chat trade updating the header and positions without a page reload.

10. **P2 — Isolate E2E state and distinguish mock language from deterministic prices.**

    References: [persistent volume](PLAN.md#docker-volume), lines 398–404; [E2E tests](PLAN.md#e2e-tests-in-test), lines 447–459.

    Reusing the normal Docker volume defeats the fresh-start assumption, and parallel tests share the same cash and watchlist. `LLM_MOCK=true` only stabilizes chat output; GBM prices and random events remain nondeterministic, so exact cash/P&L/color assertions can flake.

    Require a disposable test volume, reset between scenarios, and one worker or isolated containers per worker. Clear `MASSIVE_API_KEY` for offline tests. Use controlled quotes for exact accounting assertions, and moving-price-safe assertions for the streaming smoke test. Add container-restart persistence and chat-history reload coverage. A frontend connection-hook unit test is useful, but add a backend stream-contract test for payloads and initial reconnect snapshots; this does not require another browser E2E scenario.

11. **P2 — Make the Docker command match the local-only, unauthenticated scope.**

    References: [Docker command](PLAN.md#docker-volume), line 401; [deployment scope](PLAN.md#optional-cloud-deployment), line 424.

    `-p 8000:8000` publishes on all host interfaces by default. That can expose reset, portfolio mutation, and provider-backed chat endpoints beyond the intended local user. Use `-p 127.0.0.1:8000:8000` in the documented command and launch script; retain the necessary container-side listening address. [Docker port-publishing documentation](https://docs.docker.com/engine/network/port-publishing/).

    Also correct line 404: `finally-data:/app/db` is a Docker-managed named volume, not a mapping of the repository's `db/` directory. Document that distinction and make the backend database path independent of its working directory. The promised `.env.example` and `finally.db` ignore rules are not present in the current tracked files; include them in deployment work, along with SQLite journal/WAL files.

Add a contract/market-compatibility step before the current build order, then keep the proposed mock-chat-first implementation sequence. Fold accepted decisions back into `PLAN.md` and the market-data summary so subsequent agents have one current contract rather than contradictory definitions.
