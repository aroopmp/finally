---
last_mapped_commit: d527b54d508a1743d51a2c3cd9465cee798b6701
last_mapped_at: 2026-10-18
---
# External Integrations

**Analysis Date:** 2026-10-18

## APIs & External Services

**LLM Inference (chat assistant):**
- OpenRouter, reached through LiteLLM - Chat completions with structured output.
  - Model: `openrouter/openai/gpt-oss-120b` (`MODEL` in `backend/app/chat.py`).
  - Provider pinning: `extra_body={"provider": {"order": ["cerebras"]}}` routes inference to Cerebras.
  - Reasoning: `reasoning_effort="low"`; timeout `LLM_TIMEOUT_SECONDS = 60`.
  - SDK/Client: `litellm` (lazy import `from litellm import completion` in `call_llm`).
  - Auth: `OPENROUTER_API_KEY` env var (read by LiteLLM from the process environment; checked explicitly in `handle_chat`).
  - Structured output: Pydantic `LLMResponse` passed as `response_format`; parsed with `LLMResponseLenient.model_validate_json`.
  - Skip: when `LLM_MOCK=true`, `mock_response()` in `backend/app/chat.py` is used and no network call is made.
  - Failure behavior: missing key returns 503 `chat_unavailable`; LLM errors produce a plain error and nothing is stored.

**Market Data (optional, real prices):**
- Massive (formerly Polygon.io) REST API - Polled for live or delayed prices.
  - SDK/Client: `massive` package, `RESTClient(api_key=...)` in `backend/app/market/massive_client.py`. Calls are run with `asyncio.to_thread`.
  - Auth: `MASSIVE_API_KEY` env var. Non-empty value enables this source (`backend/app/market/factory.py`).
  - Plan selection: `MASSIVE_PLAN` (`realtime` or `delayed`, default `delayed`).
  - Endpoints used: full-market snapshot (LIVE mode) and grouped daily bars / previous closes (EOD mode, used when the plan lacks the snapshot endpoint, i.e. free Basic plan). Mode is switched on HTTP 403 "not authorized" errors.
  - Polling: 5 s (realtime) or 15 s (delayed) intervals; EOD re-checked hourly (`REALTIME_POLL_INTERVAL`, `DELAYED_POLL_INTERVAL`, `EOD_RECHECK_INTERVAL`).
  - Error mapping: 401 -> `BadKey`, 403 -> `NotAuthorized` (`classify_error` in `massive_client.py`).
  - Design reference: `planning/MASSIVE_API.md`.

**Market Data (default, no network):**
- Built-in GBM simulator - `backend/app/market/simulator.py`, seed prices in `backend/app/market/seed_prices.py`. No external calls; uses NumPy and the standard library `random`.

**Frontend to backend:**
- Same-origin `/api/*` REST calls and `EventSource` on `GET /api/stream/prices` (`backend/app/market/stream.py`). No CORS configuration; the browser never talks to third-party hosts from the app.
- Dev-only proxy: `frontend/next.config.mjs` rewrites `/api/:path*` to `http://localhost:8000/api/:path*` when `NODE_ENV=development`.

## Data Storage

**Databases:**
- SQLite (single file, embedded; no server)
  - Path: `DB_PATH` env var; default `<project>/db/finally.db`; container path `/app/db/finally.db`.
  - Schema and seed: `backend/app/schema/schema.sql`, `backend/app/schema/seed.py`, initialized by `init_db()` in `backend/app/db.py`.
  - Connection: `sqlite3` standard library. WAL mode, `foreign_keys=ON`, `BEGIN IMMEDIATE` for write transactions. No ORM and no migration tool.
  - Tables: `account`, `watchlist`, `positions`, `trades`, `portfolio_snapshots`, `chat_messages`.
  - Persistence: Docker named volume `finally-data` mounted at `/app/db` (`scripts/start_mac.sh`). `scripts/stop_mac.sh` does not remove it.

**File Storage:**
- Local filesystem only. Static frontend files are served from `STATIC_DIR` (`/app/static` in the image) by `StaticFiles` in `backend/app/main.py`.

**Caching:**
- In-memory price cache only (`backend/app/market/cache.py`, `PriceCache`). No Redis or external cache.

## Authentication & Identity

**Auth Provider:**
- None. The app is single-user with no login (`planning/PLAN.md`). No auth middleware in `backend/app/main.py`.
  - Network exposure is limited by the start script, which binds the port to `127.0.0.1:8000`.
  - API keys are the only credentials, and they are used outbound (OpenRouter, Massive).

## Monitoring & Observability

**Error Tracking:**
- None detected. No Sentry or similar SDK in `backend/pyproject.toml` or `frontend/package.json`.

**Logs:**
- Python `logging` module, `logger = logging.getLogger(__name__)` per module (e.g. `backend/app/market/factory.py`, `backend/app/chat.py`). Output goes to container stdout (`PYTHONUNBUFFERED=1`).
- Health: `GET /api/health` (`backend/app/routes.py`).

## CI/CD & Deployment

**Hosting:**
- Local Docker container. Single image built from `Dockerfile`, image name `finally`, container name `finally`, port `8000`.
- Start/stop: `scripts/start_mac.sh [--build] [--open] [--mock]` and `scripts/stop_mac.sh`.

**CI Pipeline:**
- GitHub Actions, AI-assistant workflows only:
  - `.github/workflows/claude.yml` - Runs `anthropics/claude-code-action@v1` on `@claude` mentions in issues, comments and reviews. Uses `actions/checkout@v4`.
  - `.github/workflows/claude-code-review.yml` - Runs `anthropics/claude-code-action@v1` for PR review. Uses `actions/checkout@v4`.
  - Both authenticate with the repository secret `CLAUDE_CODE_OAUTH_TOKEN` and request `id-token: write`.
- No workflow runs the backend pytest suite, frontend vitest suite, or Playwright E2E tests.
- Container registry: the Dockerfile pulls `ghcr.io/astral-sh/uv:latest` at build time (base image source). Base images: `node:20-slim`, `python:3.12-slim` (Docker Hub).

## Environment Configuration

**Required env vars:**
- `OPENROUTER_API_KEY` - Required for live chat (not required when `LLM_MOCK=true`).

**Optional env vars:**
- `MASSIVE_API_KEY` - Enables real market data when non-empty.
- `MASSIVE_PLAN` - `realtime` or `delayed` (default).
- `LLM_MOCK` - `true` for deterministic chat responses (E2E and CI).
- `DB_PATH`, `STATIC_DIR` - Path overrides (set by the Docker image).

**Secrets location:**
- Project-root `.env` file (gitignored; excluded from the Docker build context by `.dockerignore`). Template is `.env.example`. Passed into the container with `docker run --env-file .env`. For local non-Docker runs, `backend/app/config.py` loads it via `load_dotenv()`.
- GitHub Actions secret: `CLAUDE_CODE_OAUTH_TOKEN`.

## Webhooks & Callbacks

**Incoming:**
- None. The only inbound GitHub triggers are workflow events (issue comments, PR reviews) in `.github/workflows/`, not application webhooks.
- Application HTTP endpoints (for reference): `GET /api/health`, `GET /api/stream/prices` (SSE), `/api/portfolio*`, `/api/watchlist*`, `/api/chat*`, `POST /api/reset`.

**Outgoing:**
- OpenRouter API (LLM chat), via LiteLLM, only when `LLM_MOCK` is not `true`.
- Massive REST API (market data), only when `MASSIVE_API_KEY` is set.
- No outgoing webhooks or callbacks.

---

*Integration audit: 2026-10-18*
