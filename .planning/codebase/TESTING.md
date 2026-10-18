---
last_mapped_commit: d527b54d508a1743d51a2c3cd9465cee798b6701
last_mapped_at: 2026-10-18
---
# Testing Patterns

**Analysis Date:** 2026-10-18

## Test Framework

**Backend runner:**
- pytest `>=8.3.0` with `pytest-asyncio` (`>=0.24.0`) and `pytest-cov` (`>=5.0.0`), declared as the `dev` extra in `backend/pyproject.toml`.
- Config: `[tool.pytest.ini_options]` in `backend/pyproject.toml` (`testpaths = ["tests"]`, `asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope = "function"`).
- Assertion library: pytest's plain `assert`, with `pytest.raises` for exceptions. Massive SDK models are constructed for real rather than mocked (`backend/tests/market/test_massive.py`).

**Frontend runner:**
- Vitest `^3.2.7` with `jsdom` and `globals: true`. Config: `frontend/vitest.config.mts`.
- Setup file: `frontend/vitest.setup.ts` imports `@testing-library/jest-dom/vitest`.
- Libraries: `@testing-library/react` (`^16.3.3`), `@testing-library/user-event` (`^14.6.8`), `@testing-library/jest-dom` (`^7.0.1`).
- Path alias `@/` resolves to the frontend root in tests and in `tsconfig.json`.

**E2E runner:**
- Playwright (`test/package.json`), config `test/playwright.config.ts`. Runs from the host against the app container. `baseURL` defaults to `http://localhost:8000` (override via `BASE_URL`). `workers: 1`. Optional `CHROMIUM_PATH` for a preinstalled browser.

**Run commands:**

```bash
cd backend && uv run --extra dev pytest -v              # All backend tests
cd backend && uv run --extra dev pytest --cov=app       # Backend coverage (source = app, omits tests/)
cd backend && uv run --extra dev ruff check app/ tests/ # Backend lint
cd frontend && npm test                                  # vitest run (all frontend unit tests)
cd frontend && npm run lint                              # tsc --noEmit
cd test && npx playwright test                           # E2E against running container (BASE_URL)
```

Vitest watch mode: `cd frontend && npx vitest` (the `test` script runs `vitest run`, single pass). No coverage script is defined for the frontend.

## Test File Organization

**Backend location:** separate `backend/tests/` tree, mirroring the app package for the market subsystem:

```
backend/tests/
├── __init__.py
├── conftest.py              # Shared fixtures: FakeSource, client, cache, source, buy()
├── test_api.py              # REST routes: fresh start, trading, watchlist, history, reset, health
├── test_chat.py             # Chat: mock mode, action results, LLM failure, malformed output
├── test_db.py               # Schema init, constraints, transaction rollback, snapshot retention
└── market/
    ├── __init__.py
    ├── test_cache.py
    ├── test_factory.py      # Source selection via env (patch.dict(os.environ, ...))
    ├── test_massive.py      # Massive REST parsing with real SDK model objects
    ├── test_models.py
    ├── test_simulator.py    # GBMSimulator behavior
    ├── test_simulator_source.py
    ├── test_stream.py       # SSE router
    └── test_timeutil.py
```

Naming: `test_<module or behavior>.py`. Test functions are `test_<behavior in snake_case>`. Test classes group related behavior and are prefixed `Test` or are named by subject (`class TestBuySell:`, `class TestGBMSimulator:`).

**Frontend location:** a single top-level `frontend/__tests__/` directory, not co-located:

```
frontend/__tests__/
├── components.test.tsx   # Component rendering and CRUD
├── flash.test.tsx        # FlashPrice up/down and 500ms expiry
├── format.test.ts        # lib/format helpers, livePosition, treemapData, snapshotPoints
├── panels.test.tsx       # Watchlist, TradeBar, PositionsTable with a mocked context
└── stream.test.tsx       # usePriceStream, appendPoint, statusFromReadyState
```

**E2E location:** `test/tests/app.spec.ts` (single spec file for the main scenarios). Playwright config at `test/playwright.config.ts`.

## Test Structure

**Backend suite organization** (`backend/tests/test_api.py`):

```python
"""API tests: fresh start, trading, watchlist, history, reset, health."""

from tests.conftest import buy

def portfolio(client):
    return client.get("/api/portfolio").json()

def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"

class TestBuySell:
    def test_buy_reduces_cash_and_creates_position(self, client):
        r = buy(client, "AAPL", 10)
        assert r.status_code == 200
        body = r.json()
        assert body["cash_balance"] == 9000.0
```

**Patterns:**
- Arrange through helpers in `conftest.py` (`buy(client, ticker, quantity, side)`), then act through the HTTP client, then assert on both the response and a follow-up read (`portfolio(client)`), so state is verified end to end.
- Assert concrete values (`== 9000.0`, `== "insufficient_cash"`), including the error `code` in the `{"error": {...}}` body.
- Use `pytest.raises` for unit-level exceptions; route tests assert on status code and error code instead.
- Keep one behavior per test; name it for the behavior (`test_sell_all_deletes_position`, `test_ticker_normalised`).

**Frontend suite organization** (`frontend/__tests__/flash.test.tsx`):

```tsx
describe('FlashPrice', () => {
  it('flashes up then clears after 500ms', () => {
    const { rerender } = render(<FlashPrice price={100} text="100.00" testId="p" />);
    rerender(<FlashPrice price={101} text="101.00" testId="p" />);
    expect(flash()).toBe('up');
    act(() => { vi.advanceTimersByTime(499); });
    expect(flash()).toBe('up');
    act(() => { vi.advanceTimersByTime(2); });
    expect(flash()).toBe('');
  });
});
```

**Patterns:**
- Group with `describe` per component or hook; `it` names state the observable behavior.
- Use `beforeEach` to reset shared mocks (`ctx.addTicker.mockReset().mockResolvedValue(undefined)`) and `afterEach` for teardown (`vi.useRealTimers()`, `vi.unstubAllGlobals()`).
- Query by `data-testid` (`screen.getByTestId('p')`), and check classes with `toHaveClass`.
- Wrap timer advancement and state changes in `act(...)`.
- Use `userEvent` (`await userEvent.click(...)`) for interaction rather than `fireEvent`.

**E2E suite organization** (`test/tests/app.spec.ts`):
- `test.beforeEach` calls `request.post('/api/reset')` to restore the default state, then `page.goto('/')`, then waits for the hydrated trade bar (`await expect(page.getByTestId('trade-ticker')).toHaveValue('AAPL')`).
- Each scenario is a top-level `test('...', async ({ page }) => ...)`. Selectors are `getByTestId` only.
- Use `expect.poll(...)` with an explicit `timeout` for values that change over time (price streaming).

**Setup and teardown:**
- Backend: `client` fixture builds the app with `tmp_path / "test.db"`, `snapshot_interval=3600`, a `FakeSource`, and a `PriceCache`, and yields inside `with TestClient(app) as c:`. Each test therefore gets a fresh database.
- Backend autouse fixture `_env` in `conftest.py` sets `LLM_MOCK=true` and removes `MASSIVE_API_KEY` for every test.
- Frontend: `vi.useFakeTimers()` in `beforeEach` for time-based behavior, and restored in `afterEach`.

## Mocking

**Backend framework:** `unittest.mock` (`patch`, `patch.dict`, `MagicMock`) and pytest's `monkeypatch`.

**Patterns:**

Environment-driven branches use `patch.dict(os.environ, {...}, clear=True)` (`backend/tests/market/test_factory.py`):

```python
with patch.dict(os.environ, {"MASSIVE_API_KEY": "   "}, clear=True):
    ...
```

Environment for individual tests uses `monkeypatch.setenv` / `delenv` (`backend/tests/test_chat.py`):

```python
@pytest.fixture
def real_llm(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
```

Failure injection replaces the module attribute with `monkeypatch.setattr` (`backend/tests/test_chat.py`, `test_llm_failure_executes_nothing_and_stores_nothing`):

```python
monkeypatch.setattr(chat_module, "call_llm", boom)
```

The market data source is replaced by a deterministic fake that implements the real abstract interface. This is the preferred way to fake market data (`FakeSource(MarketDataSource)` in `backend/tests/conftest.py`):

```python
class FakeSource(MarketDataSource):
    kind = SourceKind.MASSIVE_EOD

    def __init__(self, cache: PriceCache, prices=None) -> None:
        self.cache = cache
        self.prices = dict(prices or PRICES)
        ...

    def set_price(self, ticker: str, price: float) -> None:
        self.prices[ticker] = price
        if ticker in self._tickers:
            self.cache.update(ticker, price, prev_close=self.prices[ticker])
```

Use `source.set_price("AAPL", 110.0)` in a test to move the price between two trades.

Frontend hook tests replace the browser global with a fake class (`frontend/__tests__/stream.test.tsx`):

```tsx
class FakeEventSource { static instances: FakeEventSource[] = []; ... emit(data) { this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent); } }
beforeEach(() => vi.stubGlobal('EventSource', FakeEventSource));
afterEach(() => vi.unstubAllGlobals());
```

Frontend component tests mock the context hook and child components with `vi.mock` (`frontend/__tests__/panels.test.tsx`):

```tsx
vi.mock('@/components/TradingProvider', async (orig) => ({
  ...(await orig<typeof import('@/components/TradingProvider')>()),
  useTrading: () => ctx,
}));
vi.mock('@/components/Sparkline', () => ({ Sparkline: () => null }));
```

Keep the real exports via `...(await orig())` and override only the hook.

**Mock and LLM mode:** the backend never calls OpenRouter in tests. `LLM_MOCK=true` is the default for every test (autouse fixture). Tests for the real path set `LLM_MOCK=false` and inject a failing or malformed `call_llm`.

**What to mock:**
- External market data (use `FakeSource` through the `source` fixture, not the Massive SDK).
- The LLM call (`chat.call_llm`) and the OpenRouter key.
- Environment variables that choose a code path.
- Browser globals not present in jsdom (`EventSource`).
- Context hooks and heavy charting children (`Sparkline`) in component tests.

**What NOT to mock:**
- The SQLite database. Backend tests use a real temp SQLite file (`tmp_path / "test.db"`) so constraints, transactions, and retention are tested for real (`backend/tests/test_db.py`).
- The `PriceCache`. Use the real class; `FakeSource` writes into it.
- Pydantic and Massive SDK model classes. `test_massive.py` builds `GroupedDailyAgg` and `TickerSnapshot` objects deliberately, with a note that they are real objects, not `MagicMock`.
- Trade math. `execute_trade` and `_apply_trade` run unmocked through the API.

## Fixtures and Factories

**Backend shared fixtures** (`backend/tests/conftest.py`):
- `PRICES` dict: deterministic seed prices for the default ten tickers plus `PYPL` (used by `add` tests).
- `cache` returns `PriceCache()`.
- `source` returns `FakeSource(cache)`.
- `client` yields a `TestClient` for a freshly created app with a temp DB. It also sets `c.app_state = app.state.finally_state`.
- `buy(client, ticker="AAPL", quantity=10, side="buy")` is a plain helper function (not a fixture) that posts to `/api/portfolio/trade`. Import it with `from tests.conftest import buy`.

**Frontend fixtures:** inline in each test file. Note: `frontend/lib/format.ts` and `frontend/lib/types.ts` are imported by the tests and components but are not present in git HEAD's tracked file list; verify before relying on them. Quotes are built with a small factory:

```ts
const quote = (ticker: string, price: number, as_of: number) => ({
  ticker, price, prev_price: price, prev_close: price, change: 0, direction: 'flat',
  day_change: 0, day_change_pct: 0, as_of, source: 'simulator',
});
```

Put new shared frontend factories in the test file that needs them, or in `frontend/vitest.setup.ts` if several files need them.

**Location:**
- Backend: `backend/tests/conftest.py` (package-wide), and fixtures local to a file when only that file uses them (`real_llm` in `test_chat.py`).
- Frontend: inline per file.
- E2E: the app seeds its own state; the `POST /api/reset` call in `beforeEach` is the only fixture.

## Coverage

**Requirements:** no coverage threshold is enforced in config. `pytest-cov` is available; `[tool.coverage.run]` sets `source = ["app"]` and omits `tests/*`. Frontend has no coverage tooling configured.

**View coverage:**

```bash
cd backend && uv run --extra dev pytest --cov=app --cov-report=term-missing
```

Coverage exclusions already configured: `pragma: no cover`, `__repr__`, `raise AssertionError`, `raise NotImplementedError`, `if __name__ == "__main__":`, `if TYPE_CHECKING:`.

## Test Types

**Unit tests (backend):**
- `backend/tests/market/`: simulator GBM math and seeding, cache version and update rules, factory env selection, Massive response parsing and error classification, timeutil, models.
- `backend/tests/test_db.py`: schema creation and idempotency, single-row account constraint, UNIQUE ticker constraints, CHECK constraints on `side` and `role`, transaction rollback, serialized read-modify-write under threads (`ThreadPoolExecutor`), snapshot retention (`keeps most recent`).

**Integration tests (backend):**
- `backend/tests/test_api.py` and `backend/tests/test_chat.py` run the full FastAPI app against a real temp DB and `FakeSource`. They cover status codes, response shapes, per-action chat results, watchlist changes, history persistence order, empty message rejection, and LLM-failure behavior (nothing executed, nothing stored).
- Concurrency: `backend/tests/test_api.py` uses `ThreadPoolExecutor` to exercise concurrent trades, and `backend/tests/test_chat.py` covers a reset race.

**Unit tests (frontend):**
- `frontend/__tests__/format.test.ts`: money, quantity, and percent formatting (`fmtUsd`, `fmtQty`, `fmtPct`, `signClass`), plus `livePosition`, `pnlColor`, `treemapData`, `snapshotPoints`.
- `frontend/__tests__/flash.test.tsx`: flash direction and 500ms expiry.
- `frontend/__tests__/stream.test.tsx`: `appendPoint` (one point per second, out-of-order ignored), `statusFromReadyState`, and `usePriceStream` connection status (starts reconnecting, connected on open). SSE reconnection is covered here, not in E2E.
- `frontend/__tests__/panels.test.tsx`: Watchlist CRUD (lists, selects on click, adds, removes), TradeBar, PositionsTable with a mocked context.
- `frontend/__tests__/components.test.tsx`: component rendering with mock data (for example `ChatBubble`, `ChatPanel`, `describeAction`, `ConnectionDot`).

**E2E tests** (`test/tests/app.spec.ts`), run against the container started with `LLM_MOCK=true`:
- Fresh start: default watchlist rows, `$10,000.00` header cash, connection dot `data-status="connected"`.
- Add and remove a ticker (`PYPL`).
- Buy then sell `AAPL` (position row appears and disappears).
- Portfolio visualization: heatmap cell `heat-MSFT` visible, and the P&L chart has `data-points` starting with a non-zero digit.
- Mocked chat: `buy 2 NVDA` executes and shows `chat-action` and a `position-row-NVDA`.
- Streaming: `price-AAPL` text changes within 15 seconds.

## Common Patterns

**Async testing (backend):** `asyncio_mode = "auto"` means `async def test_...` runs without a marker. The `FakeSource` methods are `async def` to match the `MarketDataSource` interface (`backend/tests/conftest.py`), so async source calls are awaited directly in tests.

**Async testing (frontend):** `waitFor` (from `@testing-library/react`) is used for state that settles after a promise, and `act` wraps timer advancement (see `frontend/__tests__/panels.test.tsx` and `frontend/__tests__/flash.test.tsx`).

**Error testing (backend, HTTP):** assert the status and the stable error code, not the message text:

```python
r = buy(client, "AAPL", 101)
assert r.status_code == 400 and r.json()["error"]["code"] == "insufficient_cash"
assert portfolio(client)["cash_balance"] == 10000.0   # no side effects on failure
```

Every failed-validation test also checks that no state changed.

**Error testing (LLM and chat):** inject a failing `call_llm` through `monkeypatch.setattr`, then assert that nothing was executed and nothing was stored (`test_llm_failure_executes_nothing_and_stores_nothing`).

**Time-based testing (frontend):** `vi.useFakeTimers()` in `beforeEach`, then `act(() => { vi.advanceTimersByTime(ms); })`, testing both the just-before and just-after boundary (499 ms then 501 ms for the 500 ms flash).

**Parametrization:** `pytest.mark.parametrize` is used in the backend suite (for example `backend/tests/test_chat.py`, `backend/tests/test_api.py`, `backend/tests/market/test_timeutil.py`); prefer it over loops in new tests.

**Guidance for new tests:**
- Backend route or trade behavior: add to `backend/tests/test_api.py` in the matching class, using `client` and `buy()`.
- Backend market behavior: add to the matching file under `backend/tests/market/`.
- Frontend component: add to the matching `frontend/__tests__/*.test.tsx`, query by `data-testid`, and mock `useTrading` the way `panels.test.tsx` does.
- Browser-level behavior that needs the real container: add to `test/tests/app.spec.ts`, and keep the `beforeEach` reset.

---

*Testing analysis: 2026-10-18*
