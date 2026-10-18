---
last_mapped_commit: d527b54d508a1743d51a2c3cd9465cee798b6701
last_mapped_at: 2026-10-18
---
# Codebase Structure

**Analysis Date:** 2026-10-18

## Directory Layout

```
finally/
├── backend/                   # FastAPI uv project (Python 3.12)
│   ├── app/                   # Application package
│   │   ├── main.py            # App factory, lifespan, static mount
│   │   ├── routes.py          # REST endpoints under /api
│   │   ├── chat.py            # LLM chat orchestration
│   │   ├── db.py              # SQLite connection and transaction helpers
│   │   ├── state.py           # AppState (db path, cache, source, lock, epoch)
│   │   ├── config.py          # Env-driven constants and paths
│   │   ├── errors.py          # ApiError
│   │   ├── market/            # Market data sources, cache, SSE stream
│   │   ├── services/          # Domain logic (trading, portfolio, watchlist, lifecycle)
│   │   └── schema/            # schema.sql, seed.py, create/seed helpers
│   ├── tests/                 # pytest suites (api, chat, db, market/)
│   ├── API.md                 # Backend API reference
│   ├── CLAUDE.md              # Backend-specific agent notes
│   ├── README.md
│   ├── pyproject.toml         # uv project definition
│   ├── uv.lock
│   └── market_data_demo.py    # Standalone market-layer demo
├── frontend/                  # Next.js TypeScript project (static export)
│   ├── app/                   # App Router: layout.tsx, page.tsx, globals.css
│   ├── components/            # React UI components (see list below)
│   ├── hooks/                 # usePriceStream.ts
│   ├── __tests__/             # Vitest + Testing Library tests
│   ├── next.config.mjs, tsconfig.json, tailwind.config.ts, postcss.config.js, vitest.*
│   └── package.json, package-lock.json
├── planning/                  # Project-wide agent documentation
│   ├── PLAN.md                # Specification (source of truth)
│   ├── REVIEW.md              # Open contract-level findings
│   ├── MARKET_*.md, MASSIVE_API.md
│   └── archive/               # Superseded market-data docs
├── scripts/
│   ├── start_mac.sh           # Build (if needed) and run container
│   └── stop_mac.sh            # Stop and remove container; keep volume
├── test/                      # Playwright E2E (run from host)
│   ├── playwright.config.ts
│   ├── tests/app.spec.ts
│   └── package.json
├── db/                        # Runtime volume mount target
│   └── .gitkeep               # finally.db is created here at runtime (gitignored)
├── .github/workflows/         # claude.yml, claude-code-review.yml
├── .claude/                   # Claude Code settings, agents, commands (GSD tooling)
├── Dockerfile                 # Multi-stage: node:20-slim -> python:3.12-slim
├── .dockerignore
├── .env.example               # Template for .env (gitignored)
├── .gitignore
├── CLAUDE.md                  # Project instructions for agents
└── README.md
```

## Directory Purposes

**`backend/app/`:**
- Purpose: all server code (HTTP, services, chat, persistence, market data).
- Contains: Python modules and subpackages listed below.
- Key files: `backend/app/main.py`, `backend/app/routes.py`, `backend/app/chat.py`, `backend/app/state.py`

**`backend/app/market/`:**
- Purpose: market data abstraction, simulator, Massive client, price cache, SSE.
- Contains: `interface.py`, `models.py`, `cache.py`, `simulator.py`, `massive_client.py`, `factory.py`, `stream.py`, `seed_prices.py`, `tickers.py`, `timeutil.py`, `__init__.py` (public exports).
- Key files: `backend/app/market/interface.py`, `backend/app/market/factory.py`, `backend/app/market/stream.py`

**`backend/app/services/`:**
- Purpose: business rules shared by routes and chat.
- Contains: `trading.py` (`execute_trade`), `portfolio.py` (valuation, snapshots, history), `watchlist.py` (add/remove, tracked set), `lifecycle.py` (reset), `__init__.py`.
- Key files: `backend/app/services/trading.py`

**`backend/app/schema/`:**
- Purpose: schema definition and seed data (kept beside the code that applies it).
- Contains: `schema.sql`, `seed.py` (`now_iso`, `new_id`, `create_schema`, `seed_defaults`, `insert_default_watchlist`, `insert_snapshot`), `__init__.py` re-exports.
- Key files: `backend/app/schema/schema.sql`, `backend/app/schema/seed.py`

**`backend/tests/`:**
- Purpose: pytest suites mirroring the app layout.
- Contains: `conftest.py`, `test_api.py`, `test_chat.py`, `test_db.py`, `market/` (`test_cache.py`, `test_factory.py`, `test_massive.py`, `test_models.py`, `test_simulator.py`, `test_simulator_source.py`, `test_stream.py`, `test_timeutil.py`).
- Key files: `backend/tests/conftest.py`

**`frontend/components/`:**
- Purpose: UI panels and providers.
- Contains: `TradingProvider.tsx` (context), `Header.tsx`, `Watchlist.tsx`, `MainChart.tsx`, `PriceChart.tsx`, `Sparkline.tsx`, `PositionsTable.tsx`, `TradeBar.tsx`, `ChatPanel.tsx`, `Heatmap.tsx`, `PnLChart.tsx`, `FlashPrice.tsx`.
- Key files: `frontend/components/TradingProvider.tsx`

**`frontend/hooks/`:**
- Purpose: reusable client hooks.
- Contains: `usePriceStream.ts` (EventSource connection, per-ticker series, connection status).

**`frontend/__tests__/`:**
- Purpose: frontend unit tests (components, flash animation, panels, stream hook, formatting).
- Key files: `frontend/__tests__/stream.test.tsx`, `frontend/__tests__/components.test.tsx`

**`planning/`:**
- Purpose: shared contract docs for agents. `PLAN.md` is authoritative; `REVIEW.md` lists open findings.
- Generated: No. Committed: Yes.

**`test/`:**
- Purpose: Playwright E2E tests against the running container (`baseURL=http://localhost:8000`, `LLM_MOCK=true`).
- Contains: `playwright.config.ts`, `tests/app.spec.ts`, its own `package.json`.

**`db/`:**
- Purpose: runtime SQLite location (`db/finally.db`), mounted into the container at `/app/db`.
- Generated: Yes (`finally.db`, plus WAL sidecar files). Committed: Only `.gitkeep`.

**`.claude/`:**
- Purpose: Claude Code and GSD workflow configuration (agents, commands, skills, settings). Not application code.

## Key File Locations

**Entry Points:**
- `backend/app/main.py`: `create_app` and `create_default_app` (uvicorn factory).
- `frontend/app/page.tsx`: SPA root.

**Configuration:**
- `backend/app/config.py`: constants (`STARTING_CASH`, `SNAPSHOT_INTERVAL_SECONDS`, `SNAPSHOT_RETENTION`, `CHAT_HISTORY_LIMIT`, `DEFAULT_WATCHLIST`) and env readers.
- `backend/pyproject.toml`: Python dependencies and tooling.
- `frontend/next.config.mjs`: static export and dev `/api` rewrites.
- `.env.example`: documented environment variables.

**Core Logic:**
- `backend/app/services/trading.py`: trade execution and fills.
- `backend/app/services/portfolio.py`: valuation, snapshots, history.
- `backend/app/chat.py`: LLM contract and orchestration.
- `backend/app/market/simulator.py`: GBM simulator.
- `backend/app/market/massive_client.py`: Massive REST polling.

**Persistence:**
- `backend/app/schema/schema.sql`: table definitions.
- `backend/app/db.py`: connection and transaction helpers.

**Testing:**
- `backend/tests/`: pytest.
- `frontend/__tests__/`: Vitest.
- `test/tests/app.spec.ts`: Playwright E2E.

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (`massive_client.py`, `test_simulator_source.py`).
- React components: `PascalCase.tsx` (`TradeBar.tsx`, `PnLChart.tsx`), one component per file.
- Hooks: `useCamelCase.ts` (`usePriceStream.ts`).
- Tests: `test_*.py` in backend; `*.test.ts(x)` in frontend; `*.spec.ts` in `test/`.
- Planning docs: `UPPERCASE.md`.

**Directories:**
- Backend packages: lowercase single words or `snake_case` (`market`, `services`, `schema`).
- Frontend: lowercase plural for groups (`components`, `hooks`).

**Code identifiers:**
- Python: `snake_case` functions, `PascalCase` classes, `UPPER_CASE` constants, leading underscore for private helpers (`_apply_trade`, `_generate_events`).
- Service functions take `state: AppState` as the first argument.

## Where to Add New Code

**New REST endpoint:**
- Route: add to `backend/app/routes.py` (thin; parse body, call service, return dict).
- Business logic: new or existing function in `backend/app/services/<domain>.py`, executed inside `async with state.lock:` when it mutates state.
- Tests: `backend/tests/test_api.py` (status codes and shapes).

**New trade or portfolio rule:**
- Implementation: `backend/app/services/trading.py` or `backend/app/services/portfolio.py`.
- Tests: `backend/tests/test_api.py`, with DB-level cases in `backend/tests/test_db.py`.

**New watchlist or lifecycle behaviour:**
- Implementation: `backend/app/services/watchlist.py` or `backend/app/services/lifecycle.py`.

**New LLM action type:**
- Schema: extend models in `backend/app/chat.py`; execution in `_run_actions`; mock rule in `mock_response`.
- Tests: `backend/tests/test_chat.py`.

**New market data provider:**
- Implementation: `backend/app/market/<name>_client.py` subclassing `MarketDataSource`; register in `backend/app/market/factory.py`.
- Tests: `backend/tests/market/test_<name>.py` and conformance checks alongside `test_simulator_source.py`.

**New database table or column:**
- Schema: `backend/app/schema/schema.sql`; seed changes in `backend/app/schema/seed.py`. Keep `init_db` idempotent (`CREATE TABLE IF NOT EXISTS`).

**New frontend component:**
- Implementation: `frontend/components/<Name>.tsx`; wire into `frontend/app/page.tsx` or `TradingProvider.tsx`.
- Tests: `frontend/__tests__/`.

**New frontend data hook:**
- Implementation: `frontend/hooks/use<Name>.ts`.

**Shared frontend helpers:**
- Expected location: `frontend/lib/` (`api.ts`, `types.ts`, `format.ts`), imported as `@/lib/*`. Note: this directory is referenced by tracked sources and tests but is absent from `frontend/` at HEAD; restore or recreate it before building.

**Utilities (backend):**
- Ticker and time helpers: `backend/app/market/tickers.py`, `backend/app/market/timeutil.py`. Cross-cutting helpers without a clear home go in `backend/app/config.py` or a new module under `backend/app/`.

**Planning docs:**
- `planning/` only; do not add code there.

## Special Directories

**`db/`:**
- Purpose: SQLite runtime file.
- Generated: Yes at runtime.
- Committed: `.gitkeep` only; `finally.db` is gitignored.

**`backend/static/`:**
- Purpose: Next.js static export copied in by the Dockerfile (`COPY --from=frontend /build/out /app/static` in the image). Served by `StaticFiles` mount in `backend/app/main.py` when present.
- Generated: Yes. Committed: No.

**`frontend/node_modules/`, `frontend/out/`, `frontend/.next/`:**
- Purpose: npm install and build artifacts.
- Generated: Yes. Committed: No (ignored by `frontend/.gitignore`).

**`backend/.venv/`, `backend/__pycache__/`:**
- Purpose: uv environment and bytecode.
- Generated: Yes. Committed: No.

**`.planning/`:**
- Purpose: GSD planning and codebase maps (this directory).
- Committed: per project workflow.

---

*Structure analysis: 2026-10-18*
