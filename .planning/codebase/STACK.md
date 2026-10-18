---
last_mapped_commit: d527b54d508a1743d51a2c3cd9465cee798b6701
last_mapped_at: 2026-10-18
---
<!-- refreshed: 2026-10-18 -->

# Technology Stack

**Analysis Date:** 2026-10-18

## Languages

**Primary:**
- Python 3.12+ - All server logic: FastAPI app, SQLite access, market data, trading, LLM chat. Declared in `backend/pyproject.toml` (`requires-python = ">=3.12"`). Runtime image is `python:3.12-slim` (`Dockerfile`).
- TypeScript 5.9 (`typescript ^5.9.3`) - Frontend SPA in `frontend/` (`app/`, `components/`, `hooks/`). Source is tracked in git HEAD; the working tree `frontend/` directory is currently empty (see `git show HEAD:frontend/<path>`).

**Secondary:**
- SQL (SQLite dialect) - Schema in `backend/app/schema/schema.sql`.
- Bash - Launch/stop wrappers in `scripts/start_mac.sh` and `scripts/stop_mac.sh`.
- CSS (Tailwind utilities + `globals.css`) - Styling, `frontend/app/globals.css`, `frontend/tailwind.config.ts`.
- JavaScript (ESM) - `frontend/next.config.mjs`, `frontend/postcss.config.js`.

## Runtime

**Environment:**
- Python 3.12 (container: `python:3.12-slim`; local: `.venv` under `backend/.venv`).
- Node.js 20 (container build stage: `node:20-slim` in `Dockerfile`) - used only to build the static frontend; Node is not present in the runtime image.
- Playwright E2E runs on the host with Node (no version pinned beyond the lockfile).

**Package Managers:**
- `uv` (Python) - Copied into the image from `ghcr.io/astral-sh/uv:latest`. Project file `backend/pyproject.toml`, lockfile `backend/uv.lock` (present). Build backend: `hatchling`.
- `npm` (Node) - Frontend uses `frontend/package-lock.json` (present; `npm ci` in Docker). E2E uses `test/package-lock.json` (present).
- Lockfiles: present for all three (`backend/uv.lock`, `frontend/package-lock.json`, `test/package-lock.json`).

## Frameworks

**Core (backend):**
- FastAPI `0.128.7` (locked; spec `>=0.115.0`) - REST routes, SSE streaming (`StreamingResponse`, `media_type="text/event-stream"` in `backend/app/market/stream.py`), static file mount in `backend/app/main.py`.
- Starlette `0.52.1` (transitive via FastAPI) - `StaticFiles(html=True)` serving the Next.js export.
- Uvicorn `0.40.0` with `[standard]` extras (spec `>=0.32.0`) - ASGI server, started by the container `CMD`.
- Pydantic `2.12.5` (spec `>=2.12.5`) - Request models and LLM structured-output schemas (`backend/app/chat.py`).

**Core (frontend):**
- Next.js `^15.5.28` - Built with `output: 'export'` and `images: { unoptimized: true }` (`frontend/next.config.mjs`). Produces static `out/` consumed by Docker stage 1.
- React `^19.3.1` and React DOM `^19.3.1`.
- Lightweight Charts `^4.2.3` - Canvas charts for the main chart, sparklines and P&L chart.
- Recharts `^2.15.4` - SVG `Treemap` for the portfolio heatmap.
- Tailwind CSS `^3.4.19` with PostCSS and Autoprefixer (`frontend/tailwind.config.ts`, `frontend/postcss.config.js`).

**LLM:**
- LiteLLM `1.105.1` (spec `>=1.105.1`) - Imported lazily in `backend/app/chat.py` (`from litellm import completion`). Model `openrouter/openai/gpt-oss-120b`, routed to Cerebras via `extra_body={"provider": {"order": ["cerebras"]}}`.

**Market data:**
- `massive` SDK `2.2.0` (spec `>=1.0.0`) - `RESTClient` used by `backend/app/market/massive_client.py`.
- NumPy `2.4.2` (spec `>=2.0.0`) - GBM math in `backend/app/market/simulator.py`.
- Rich `>=13.0.0` - Console output in `backend/market_data_demo.py` only.

**Testing:**
- pytest `9.0.2` (spec `>=8.3.0`), pytest-asyncio (`>=0.24.0`, `asyncio_mode = "auto"`), pytest-cov (`>=5.0.0`) - Backend, config in `backend/pyproject.toml`. Tests in `backend/tests/`.
- Vitest `^3.2.7` with jsdom `^30.1.3` and `@vitejs/plugin-react` `^6.1.2` - Frontend unit tests. Config: `frontend/vitest.config.mts`, setup `frontend/vitest.setup.ts`. Tests in `frontend/__tests__/`.
- React Testing Library `^16.3.3`, `@testing-library/user-event ^14.6.8`, `@testing-library/jest-dom ^7.0.1`.
- Playwright `^1.49.0` (`@playwright/test`) - E2E in `test/`, config `test/playwright.config.ts`, spec `test/tests/app.spec.ts`. Runs from host against `http://localhost:8000` (overridable via `BASE_URL`; optional `CHROMIUM_PATH`).

**Build/Dev:**
- hatchling - Python wheel build backend (`[tool.hatch.build.targets.wheel] packages = ["app"]`).
- Ruff `0.15.0` (spec `>=0.7.0`) - Lint (`select = E,F,I,N,W`) and format, line length 100, target py312.
- TypeScript `tsc --noEmit` as the frontend `lint` script (`frontend/package.json`). No ESLint/Prettier config detected.
- `@types/node ^26.6.5`, `@types/react ^19.3.0`, `@types/react-dom ^19.3.0`.

## Key Dependencies

**Critical:**
- `fastapi` / `starlette` / `uvicorn[standard]` - The entire HTTP surface (REST, SSE, static), single port 8000.
- `litellm` `1.105.1` - Sole LLM client; large transitive dependency tree. Lazy import keeps startup fast.
- `massive` `2.2.0` - Real market data client; only imported when `MASSIVE_API_KEY` is set.
- `numpy` `2.4.2` - Required at import time by the simulator (`backend/app/market/simulator.py`), so it is needed even in default simulator mode.
- `next` `^15.5.28` and `lightweight-charts` `^4.2.3` - Frontend shell and charting.
- `sqlite3` (Python standard library) - Database driver; no ORM. See `backend/app/db.py`.

**Infrastructure:**
- `httpx` `0.28.1` - Declared as a direct dependency (`backend/pyproject.toml`); HTTP client used alongside the SDKs.
- `pydantic` `2.12.5` - Validation for API and LLM payloads.
- `rich` - Demo script output only; not used by the server.

## Configuration

**Environment variables** (read by the backend from the process environment):
- `OPENROUTER_API_KEY` - Required unless `LLM_MOCK=true`. Read in `backend/app/chat.py`; missing key yields a 503 `chat_unavailable`.
- `MASSIVE_API_KEY` - Optional. Non-empty selects `MassiveDataSource`; empty selects the simulator (`backend/app/market/factory.py`).
- `MASSIVE_PLAN` - Optional, default `delayed`; `realtime` selects a 5 s poll interval, otherwise 15 s (`backend/app/market/factory.py`, `massive_client.py`).
- `LLM_MOCK` - `true` enables deterministic chat replies (`backend/app/config.py`, `llm_mock()`).
- `DB_PATH` - SQLite file location. Default `<project>/db/finally.db`; the image sets `/app/db/finally.db`.
- `STATIC_DIR` - Static frontend directory. Default `<project>/backend/static`; the image sets `/app/static`.
- `PYTHONUNBUFFERED=1` - Set in the image.

**Env file handling:**
- `.env.example` is committed. It defines `OPENROUTER_API_KEY`, `MASSIVE_API_KEY` and `LLM_MOCK`. A real `.env` exists at the project root and is gitignored and excluded from Docker build context (`.dockerignore`). Its contents are intentionally not recorded here.
- `backend/app/config.py` provides `load_dotenv()` for local non-Docker runs only (uses `setdefault`, so the real environment wins).
- Docker receives env via `--env-file .env` from `scripts/start_mac.sh`; `scripts/start_mac.sh --mock` adds `-e LLM_MOCK=true`.

**Application constants** (`backend/app/config.py`):
- `STARTING_CASH = 10_000.0`, `SNAPSHOT_INTERVAL_SECONDS = 30.0`, `SNAPSHOT_RETENTION = 20_000`, `CHAT_HISTORY_LIMIT = 20`, `DEFAULT_WATCHLIST` (10 tickers).
- Simulator tick interval 0.5 s (`backend/app/market/simulator.py`, `backend/app/market/stream.py`).
- LLM timeout 60 s (`LLM_TIMEOUT_SECONDS` in `backend/app/chat.py`).

**Database configuration:**
- SQLite via `sqlite3.connect(..., timeout=10, isolation_level=None)`, WAL journal mode, `foreign_keys=ON` (`backend/app/db.py`).
- Lazy init: `init_db()` creates the schema and seeds defaults (`backend/app/schema/schema.sql`, `backend/app/schema/seed.py`).

**Build configuration:**
- `backend/pyproject.toml` - Python project, dependencies, pytest, ruff and coverage config.
- `backend/uv.lock` - Reproducible Python lockfile (`uv sync --frozen --no-dev` in the image).
- `frontend/next.config.mjs` - Static export; dev-only `/api/*` rewrite to `http://localhost:8000`.
- `frontend/tsconfig.json` - `strict`, `moduleResolution: bundler`, path alias `@/*`.
- `frontend/vitest.config.mts` - jsdom environment, globals.
- `Dockerfile` - Two stages: `node:20-slim` (frontend `npm ci && npm run build`, output `/build/out`) and `python:3.12-slim` (uv sync, copies `backend/app` and the static export). Exposes 8000. Healthcheck calls `/api/health`. CMD: `uvicorn app.main:create_default_app --factory --host 0.0.0.0 --port 8000`.
- `.dockerignore` - Excludes `.git`, `.env`, `db/*.db`, `node_modules`, `frontend/.next`, `frontend/out`, `__pycache__`, `.venv`, `backend/tests`, `test`.
- `.github/workflows/` - CI automation only (no build/test pipeline detected); see INTEGRATIONS.md.

## Platform Requirements

**Development:**
- Python 3.12+ and `uv` for backend work (`cd backend && uv sync`).
- Node.js 20+ and npm for frontend work (`frontend/`), and for E2E in `test/`.
- Docker for the packaged app; macOS/Linux shell for `scripts/start_mac.sh` and `scripts/stop_mac.sh` (uses `curl`, optional `open`/`xdg-open`).
- Optional: a Chromium build for Playwright (or `CHROMIUM_PATH`).

**Production:**
- Single Docker container, single process (uvicorn, no worker count set), port 8000 bound to `127.0.0.1` by the start script.
- Named Docker volume `finally-data` mounted at `/app/db` for the SQLite file.
- No external database server; no cloud deployment target configured.

---

*Stack analysis: 2026-10-18*
