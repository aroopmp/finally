---
last_mapped_commit: d527b54d508a1743d51a2c3cd9465cee798b6701
last_mapped_at: 2026-10-18
---
<!-- refreshed: 2026-10-18 -->

# Architecture

**Analysis Date:** 2026-10-18

## System Overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│                 Browser (Next.js static export, SPA)                 │
│   frontend/app/page.tsx  ->  components/*  ->  hooks/usePriceStream  │
└───────────────┬───────────────────────────────────────┬──────────────┘
                │ fetch /api/*                          │ EventSource /api/stream/prices
                ▼                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FastAPI app  `backend/app/main.py` (create_app / create_default_app)│
│  ├── REST router        `backend/app/routes.py`      (prefix /api)   │
│  ├── SSE router         `backend/app/market/stream.py` (/api/stream) │
│  └── StaticFiles mount  "/" -> backend/static (Next export)          │
└───────┬──────────────────────┬───────────────────────┬───────────────┘
        │                      │                       │
        ▼                      ▼                       ▼
┌───────────────┐   ┌────────────────────────┐  ┌────────────────────┐
│ Services      │   │ Chat                   │  │ Market data        │
│ `services/*`  │   │ `backend/app/chat.py`  │  │ `app/market/*`     │
│ trading,      │   │ LiteLLM -> OpenRouter  │  │ MarketDataSource   │
│ portfolio,    │   │ (LLM_MOCK bypass)      │  │ Simulator | Massive│
│ watchlist,    │   └──────────┬─────────────┘  └─────────┬──────────┘
│ lifecycle     │              │ executes via             │ writes
└───────┬───────┘              │ services.trading         ▼
        │                      │               ┌────────────────────┐
        ▼                      ▼               │ PriceCache (memory)│
┌──────────────────────────────────────────┐   │ `market/cache.py`  │
│ SQLite  `db/finally.db` (WAL)            │   └────────────────────┘
│ `app/db.py` + `app/schema/*`             │
└──────────────────────────────────────────┘
```

## Pattern Overview

**Overall:** Single-process modular monolith. One FastAPI app serves the JSON API, the SSE price stream, and the statically exported Next.js frontend on port 8000. A single in-process background market-data task feeds a shared in-memory price cache; all state changes go through service functions that run under one asyncio lock.

**Key Characteristics:**
- One origin, one port, one container: the frontend makes same-origin `/api/*` calls; no CORS.
- Dependency injection by factory, not globals: `create_app()` in `backend/app/main.py` takes `db_path`, `source`, `cache`, `snapshot_interval`, `static_dir`; `create_stream_router(cache)` in `backend/app/market/stream.py` receives the cache. Tests build isolated apps this way.
- Per-request state access: routes fetch `AppState` from `request.app.state.finally_state` via `_state()` in `backend/app/routes.py`.
- Serialised mutations: `AppState.lock` (`backend/app/state.py`) guards trades, watchlist changes, chat-executed actions, snapshot writes and reset.
- Strategy pattern for market data: `MarketDataSource` ABC in `backend/app/market/interface.py` with `SimulatorDataSource` (`backend/app/market/simulator.py`) and `MassiveDataSource` (`backend/app/market/massive_client.py`), selected by `create_market_data_source()` in `backend/app/market/factory.py` from `MASSIVE_API_KEY`.
- Producer/consumer decoupling: sources push into `PriceCache`; SSE and valuation code read the cache and never call providers directly.
- Lazy schema: `init_db()` in `backend/app/db.py` runs on app startup (FastAPI lifespan), creating tables and seeding defaults idempotently.

## Layers

**Entry / composition (`backend/app/main.py`, `backend/app/config.py`):**
- Purpose: build the app, wire lifespan (DB init, market source start/stop, snapshot loop), register error handlers, mount routers and static files.
- Location: `backend/app/main.py`, `backend/app/config.py`, `backend/app/errors.py`
- Contains: `create_app`, `create_default_app` (uvicorn factory), `_snapshot_loop`, `ApiError` handler, `RequestValidationError` -> 400 mapping, env readers (`db_path()`, `static_dir()`, `llm_mock()`, `load_dotenv()`).
- Depends on: every other layer.
- Used by: uvicorn (`CMD` in `Dockerfile`: `app.main:create_default_app --factory`).

**HTTP routes (`backend/app/routes.py`):**
- Purpose: thin adapters from HTTP to services. Parse Pydantic bodies, call one service function, return JSON.
- Location: `backend/app/routes.py`
- Contains: `router = APIRouter(prefix="/api")`, `TradeRequest`, `WatchlistAdd`, `ChatRequest`, endpoints for health, portfolio, trade, history, reset, watchlist, chat, chat history.
- Depends on: `services/*`, `chat.py`, `db.read`, `state.AppState`.
- Used by: FastAPI app via `app.include_router(router)`.

**Services (business logic) (`backend/app/services/`):**
- Purpose: all domain rules: quantity parsing, fills, cash and position math, snapshots, watchlist normalisation, reset.
- Location: `backend/app/services/trading.py`, `portfolio.py`, `watchlist.py`, `lifecycle.py`
- Contains: `execute_trade()` (the single shared trade path for manual and chat trades), `_apply_trade()`, `parse_quantity()`, `get_portfolio()`, `total_value()`, `record_snapshot()`, `trim_snapshots()`, `history()`, `add_ticker()`, `remove_ticker()`, `tracked_set()`, `reconcile_tracked()`, `reset_account()`.
- Depends on: `db.transaction` / `db.read`, `market` (cache and quotes), `schema` helpers, `state.AppState`.
- Used by: `routes.py`, `chat.py`, `main.py` (snapshot loop via `portfolio.record_snapshot`).

**Chat / LLM (`backend/app/chat.py`):**
- Purpose: build portfolio context, load recent history, call the LLM, validate structured output, run returned actions through services, persist the exchange.
- Location: `backend/app/chat.py`
- Contains: Pydantic response models `ChatTrade`, `WatchlistChange`, `LLMResponse`, `LLMResponseLenient`; `mock_response()` (deterministic LLM_MOCK path); `call_llm()` (LiteLLM -> OpenRouter, lazily imported); `build_context()`, `load_history()`, `_run_actions()`, `handle_chat()`.
- Depends on: `services.trading`, `services.watchlist`, `db`, `state`, `config`.
- Used by: `routes.post_chat`, `routes.get_chat_history`.

**Persistence (`backend/app/db.py`, `backend/app/schema/`):**
- Purpose: SQLite access and schema lifecycle.
- Location: `backend/app/db.py`, `backend/app/schema/__init__.py`, `backend/app/schema/schema.sql`, `backend/app/schema/seed.py`
- Contains: `connect()` (WAL, foreign keys, 10s timeout, autocommit isolation), `transaction()` (`BEGIN IMMEDIATE` write lock), `read()`, `init_db()`, `create_schema()`, `seed_defaults()`, `insert_default_watchlist()`, `insert_snapshot()`, `now_iso()`, `new_id()`.
- Depends on: stdlib `sqlite3` only.
- Used by: services, chat, lifespan.

**Market data (`backend/app/market/`):**
- Purpose: produce, cache and stream prices; answer quote and EOD queries.
- Location: `backend/app/market/`
- Contains: `interface.py` (`MarketDataSource`, `FRESH_LIMIT` staleness table), `models.py` (`Quote`, `SourceKind`, `SourceStatus`, `UnknownTicker`, `QuoteUnavailable`), `cache.py` (`PriceCache`), `simulator.py` (`GBMSimulator`, `SimulatorDataSource`, `_env_seed()`), `massive_client.py` (`MassiveDataSource`, `Mode`, `pick_price`, error classifiers), `seed_prices.py`, `tickers.py` (`normalize_ticker`), `timeutil.py`, `stream.py` (`create_stream_router`, `_generate_events`), `factory.py`.
- Depends on: `httpx`/Massive SDK only for the Massive path; no DB or HTTP-layer imports.
- Used by: `main.py` (lifespan, router), `services` (quotes, valuation), `chat` (prices in context).

**Frontend (`frontend/`, tracked in git HEAD):**
- Purpose: single-page trading terminal, statically exported.
- Location: `frontend/app/` (Next.js App Router: `layout.tsx`, `page.tsx`, `globals.css`), `frontend/components/`, `frontend/hooks/usePriceStream.ts`
- Contains: `TradingProvider.tsx` (React context holding portfolio, watchlist, snapshots, live positions, and the SSE stream), `Watchlist`, `MainChart`, `PriceChart`, `Sparkline`, `PositionsTable`, `TradeBar`, `ChatPanel`, `Header`, `Heatmap`, `PnLChart`, `FlashPrice`.
- Depends on: `/api/*` and `/api/stream/prices` only.
- Used by: browser; the built output is copied into `backend/static` (served by the `StaticFiles` mount).

## Data Flow

### Primary Request Path (manual trade)

1. Browser `TradeBar` calls `api` (from `@/lib/api`, not present in the working tree) with `POST /api/portfolio/trade` (`backend/app/routes.py:post_trade`).
2. Pydantic validates `TradeRequest`; invalid shape is mapped to 400 by the `RequestValidationError` handler in `backend/app/main.py`.
3. `trading.execute_trade()` (`backend/app/services/trading.py:52`) normalises the ticker, parses quantity (`parse_quantity`), and fetches a fresh quote via `_fill_quote()` (`backend/app/services/trading.py:35`) from the price cache/source.
4. Under `state.lock` and a `transaction()` (`BEGIN IMMEDIATE`), `_apply_trade()` (`backend/app/services/trading.py:65`) updates `account`, `positions`, appends `trades`, and records a `portfolio_snapshots` row.
5. The result dict is returned as JSON; the frontend refreshes portfolio state.

### Price Streaming Path

1. Lifespan starts the market source with `sorted(tracked_set(state))` (`backend/app/main.py`, `backend/app/services/watchlist.py:31`). Tracked set = watchlist ∪ open positions.
2. The source (simulator tick loop or Massive poller) writes `Quote`s into `PriceCache.update()` (`backend/app/market/cache.py`).
3. `GET /api/stream/prices` (`backend/app/market/stream.py:_generate_events`) reads the cache about every 500 ms and emits SSE `data:` events with a `retry:` directive.
4. `usePriceStream` (`frontend/hooks/usePriceStream.ts`) consumes via `EventSource`, accumulates chart series per ticker since page load, and exposes connection status.

### Chat Path

1. `POST /api/chat` -> `chat.handle_chat()` (`backend/app/chat.py:243`).
2. `build_context()` assembles cash, positions with P&L, watchlist prices, total value; `_llm_history()` loads the last `CHAT_HISTORY_LIMIT` (20) messages.
3. `call_llm()` (`backend/app/chat.py:102`) returns a parsed `LLMResponse`, or `mock_response()` when `LLM_MOCK=true`. On LLM failure nothing executes and nothing is stored.
4. `_run_actions()` (`backend/app/chat.py:204`) executes trades via `services.trading.execute_trade` and watchlist changes via `services.watchlist`, in order, collecting per-action results; a failed action does not stop later ones.
5. The user and assistant messages plus actions are persisted to `chat_messages`; the JSON response is returned. `state.epoch` is compared so replies from before a reset are discarded.

### Snapshot Path

1. `_snapshot_loop` (`backend/app/main.py`) sleeps `SNAPSHOT_INTERVAL_SECONDS` (30 s, `backend/app/config.py`), then calls `portfolio.record_snapshot()` under `state.lock`.
2. `trim_snapshots()` keeps the newest `SNAPSHOT_RETENTION` (20,000) rows. `GET /api/portfolio/history` reads them for the P&L chart.

**State Management:**
- Server: durable state in SQLite; transient prices in `PriceCache`; concurrency via `AppState.lock`; `AppState.epoch` invalidates in-flight chat on reset.
- Client: `TradingProvider` context plus `usePriceStream` hook; no global store library in use.

## Key Abstractions

**`AppState`:**
- Purpose: per-app bundle of DB path, price cache, market source, mutation lock and reset epoch.
- Examples: `backend/app/state.py`
- Pattern: plain dataclass injected via `app.state.finally_state`.

**`MarketDataSource`:**
- Purpose: provider-agnostic contract (start/stop, add/remove ticker, get quotes, EOD closes, max quote age).
- Examples: `backend/app/market/interface.py`, `backend/app/market/simulator.py`, `backend/app/market/massive_client.py`
- Pattern: ABC with `FRESH_LIMIT` keyed by `SourceKind`.

**`PriceCache`:**
- Purpose: shared latest-price store (price, previous price, timestamp per ticker).
- Examples: `backend/app/market/cache.py`
- Pattern: in-memory map; note it defines `__len__`, so use `is None` checks, not truthiness (see comment in `create_app`).

**`Quote`:**
- Purpose: immutable price record passed between sources, cache, and services.
- Examples: `backend/app/market/models.py`
- Pattern: dataclass with `SourceKind`/`SourceStatus` enums.

**Service functions:**
- Purpose: one function per use-case, taking `AppState` first and returning plain dicts.
- Examples: `backend/app/services/trading.py`, `backend/app/services/portfolio.py`, `backend/app/services/watchlist.py`
- Pattern: `async def` for anything touching market data or the lock; sync `with transaction(...)` for pure DB work.

## Entry Points

**Web server:**
- Location: `backend/app/main.py:create_default_app`
- Triggers: `uvicorn app.main:create_default_app --factory --host 0.0.0.0 --port 8000` (container `CMD` in `Dockerfile`); local runs from `backend/`.
- Responsibilities: load `.env` for local runs, configure logging, build the app.

**Container build:**
- Location: `Dockerfile` (stage 1 `node:20-slim` builds `frontend/`; stage 2 `python:3.12-slim` installs `backend/` via `uv` and copies the export to `/app/static`).
- Triggers: `scripts/start_mac.sh` (`--build` or missing image).

**Market demo script:**
- Location: `backend/market_data_demo.py`
- Triggers: manual run; exercises the market layer outside the web app.

**Frontend root:**
- Location: `frontend/app/page.tsx` (rendered under `frontend/app/layout.tsx`)
- Triggers: browser load of `/`.

## Architectural Constraints

- **Threading:** single asyncio event loop in one uvicorn worker. The simulator and Massive poller are asyncio tasks, not threads. Blocking LiteLLM calls (`call_llm`) and sync SQLite access run on the loop; keep the container to one worker (state and cache are per-process).
- **Global state:** no module-level singletons for app state; `AppState` and `PriceCache` are created per app in `create_app`. The `PROJECT_ROOT` constant in `backend/app/config.py` is the only module-level computed path.
- **Circular imports:** none identified. Layering is one-way: `routes -> services/chat -> db/market`; `market` does not import `services`, `routes`, or `db`.
- **Single replica:** the in-memory cache and `AppState.lock` assume exactly one process; multi-worker or multi-replica deployment would break price freshness and serialisation.
- **Frontend contract:** frontend imports `@/lib/api`, `@/lib/types`, `@/lib/format`, which are referenced by tracked sources and tests but are not present in `frontend/` at HEAD; the frontend cannot build until they exist.

## Anti-Patterns

### Truthiness checks on `PriceCache`

**What happens:** `cache or PriceCache()` style defaults.
**Why it's wrong:** `PriceCache` defines `__len__`, so an empty cache is falsy and would be replaced silently.
**Do this instead:** use `is None` checks, as `create_app` does in `backend/app/main.py`.

### Bypassing `execute_trade()` or the lock

**What happens:** writing to `positions`/`account` directly from a route or chat handler.
**Why it's wrong:** breaks the shared validation and fill path that manual and LLM trades must both use, and races with the snapshot loop and reset.
**Do this instead:** call `services.trading.execute_trade()` inside `async with state.lock:` as the existing routes and `chat._run_actions` do.

### Creating app state at import time

**What happens:** module-level `app = FastAPI()` plus module-level cache/source.
**Why it's wrong:** tests cannot isolate DB path, source or cache, and the lifespan would start background tasks on import.
**Do this instead:** extend `create_app()` parameters and build state inside the lifespan, as `backend/app/main.py` does.

## Error Handling

**Strategy:** domain errors raise `ApiError` (`backend/app/errors.py`) with a status and code; FastAPI handlers convert them to `{"error": {"code", "message"}}`. Market errors (`UnknownTicker`, `QuoteUnavailable`) are translated to API errors in services.

**Patterns:**
- Request-shape errors become 400 `invalid_request` via `validation_handler` in `backend/app/main.py`.
- LLM failures (timeout, bad key, malformed JSON) are caught in `chat.handle_chat`, return a plain error, and store nothing.
- Background snapshot failures are logged with `logger.exception` and the loop continues.
- Chat actions record per-action errors in their result rather than aborting.

## Cross-Cutting Concerns

**Logging:** stdlib `logging`, `basicConfig(level=INFO)` in `create_default_app`; module loggers via `logging.getLogger(__name__)`.
**Validation:** Pydantic models at route and LLM boundaries (`routes.py`, `chat.py`); ticker normalisation in `backend/app/market/tickers.py` and `services/watchlist.parse_ticker`; quantity rules in `services/trading.parse_quantity`.
**Authentication:** none; single-user local application by design.
**Configuration:** environment variables read in `backend/app/config.py` and `backend/app/market/factory.py` (`MASSIVE_API_KEY`, `MASSIVE_PLAN`, `LLM_MOCK`, `DB_PATH`, `STATIC_DIR`, `OPENROUTER_API_KEY`).

---

*Architecture analysis: 2026-10-18*
