---
last_mapped_commit: d527b54d508a1743d51a2c3cd9465cee798b6701
last_mapped_at: 2026-10-18
---
# Coding Conventions

**Analysis Date:** 2026-10-18

## Naming Patterns

**Files:**
- Python modules are `snake_case.py`: `backend/app/services/trading.py`, `backend/app/market/massive_client.py`, `backend/app/market/seed_prices.py`.
- Python test files are `test_<subject>.py` and live under `backend/tests/` (mirrored subfolders such as `backend/tests/market/test_simulator.py`).
- React components are `PascalCase.tsx` with one exported component per file: `frontend/components/Watchlist.tsx`, `frontend/components/FlashPrice.tsx`, `frontend/components/TradingProvider.tsx`.
- Hooks are `camelCase` with a `use` prefix in `frontend/hooks/`: `frontend/hooks/usePriceStream.ts`.
- Frontend non-component modules are `camelCase.ts` or lowercase (`frontend/lib/format.ts`, `frontend/lib/types.ts` are imported by code and tests but are not tracked in git HEAD; see Testing doc).
- Frontend tests are `frontend/__tests__/<area>.test.ts(x)`.
- Playwright specs are `test/tests/<name>.spec.ts`.

**Functions (Python):**
- `snake_case`: `execute_trade`, `parse_quantity`, `_apply_trade`, `reconcile_tracked`, `watchlist_tickers`.
- Module-private helpers are prefixed with a single underscore: `_fill_quote`, `_apply_trade`, `_state`, `_env` (test fixture).
- Service functions take `state: AppState` as the first parameter (see `backend/app/services/trading.py`).

**Functions (TypeScript):**
- `camelCase` for functions and handlers: `appendPoint`, `statusFromReadyState`, `fmtUsd`, `signClass`, `pnlColor`, `onAdd`.
- Event handlers inside components are named `onXxx` (`onAdd` in `frontend/components/Watchlist.tsx`).

**Variables:**
- Python: `snake_case`; money uses `Decimal` locals named `cash`, `held`, `amount`, `avg`, `qty`.
- TypeScript: `camelCase`; refs named `history`, `prev`, `store`.

**Types and classes:**
- Python classes `PascalCase`: `AppState`, `ApiError`, `PriceCache`, `GBMSimulator`, `MassiveDataSource`, `FakeSource` (tests). Test classes are `Test*` or `TestXxx` grouping (`class TestBuySell:` in `backend/tests/test_api.py`), matching `python_classes = ["Test*"]` in `backend/pyproject.toml`.
- Pydantic request models use `PascalCase` with a suffix naming the request: `TradeRequest`, `WatchlistAdd`, `ChatRequest` in `backend/app/routes.py`.
- Python `Literal` is used for enumerated strings: `side: Literal["buy", "sell"]`.
- TypeScript interfaces and types are `PascalCase` (`PriceStream`, `ConnectionStatus`, `Point`, `PriceMap`, `Position`, `LivePosition`) and are exported from a shared types module (`@/lib/types`).
- Enum-like classes use `SourceKind`, `SourceStatus`, `Mode` (`backend/app/market/models.py`, `backend/app/market/massive_client.py`).

**Constants:**
- `UPPER_SNAKE_CASE` in both languages: `MIN_QUANTITY`, `QTY_STEP`, `DUST`, `CENT` (`backend/app/services/trading.py`), `MAX_HISTORY_POINTS` and `FLASH_MS` (`frontend/hooks/usePriceStream.ts`, `frontend/components/FlashPrice.tsx`), `SEED_PRICES` (`backend/app/market/seed_prices.py`).

**Ticker symbols:** always normalized to trimmed, uppercase, 1-5 letters `[A-Z]` through `normalize_ticker` / `parse_ticker` before use. Do not compare or store raw input.

## Code Style

**Python formatting and linting:**
- Tool: `ruff` (lint and format), configured in `backend/pyproject.toml` under `[tool.ruff]`.
- `line-length = 100`, `target-version = "py312"`.
- Lint rules selected: `E`, `F`, `I`, `N`, `W`. Ignored: `E501` (line length left to the formatter) and `N818` (exception names fixed by the interface docs).
- Run: `cd backend && uv run --extra dev ruff check app/ tests/`.
- Every Python module starts with a one-line docstring, followed by `from __future__ import annotations` when type hints reference forward types (see `backend/app/services/trading.py`, `backend/app/routes.py`, `backend/app/errors.py`).
- Use double quotes and trailing commas in multi-line calls (formatter style).

**TypeScript / React formatting:**
- No Prettier or ESLint config is tracked. The only lint script is `tsc --noEmit` (`"lint": "tsc --noEmit"` in `frontend/package.json`).
- The code uses single quotes, semicolons, and 2-space indentation; some components pack short JSX handlers on one line (`onClick={(e) => { e.stopPropagation(); ... }}`).
- `tsconfig.json` runs with `"strict": true`, `"isolatedModules": true`, and `"jsx": "preserve"`. Path alias `@/*` maps to the frontend root.
- Use `import type { ... }` for type-only imports (`import type { ConnectionStatus, Point, PriceMap } from '@/lib/types';`).

**Tailwind:**
- Styling is utility classes in JSX (`className="panel flex h-full min-h-0 flex-col"`). Theme tokens are semantic: `text-up`, `text-down`, `text-muted`, `bg-panel2`, `border-line`, `bg-bg`, `bg-secondary`, `text-primary`, and the component-level classes `panel`, `panel-title`, `num`, `price-cell`, `flash-up`, `flash-down` defined in `frontend/app/globals.css`.
- Tailwind config is `frontend/tailwind.config.ts`; colors follow the PLAN palette (accent `#ecad0a`, primary `#209dd7`, secondary `#753991`).
- Use `num` for tabular numeric output so columns align.

**Client components:**
- Files that use React hooks or browser APIs start with `'use client';` (`frontend/components/*.tsx`, `frontend/hooks/usePriceStream.ts`).

## Import Organization

**Python order** (enforced by ruff `I`):
1. Standard library (`import logging`, `from decimal import ...`)
2. Third-party (`from fastapi import ...`, `from pydantic import ...`)
3. First-party relative imports within `app/`: `from ..db import transaction`, `from ..errors import ApiError`, `from ..market import Quote, ...`

Inside `backend/app/` use relative imports (`from ..state import AppState`). Tests import absolute from the package: `from app.market import PriceCache` and `from tests.conftest import buy`.

**TypeScript order** (observed):
1. React and framework (`import { useEffect, useRef, useState } from 'react';`, `next`, `recharts`)
2. Testing libraries in tests (`@testing-library/react`, `vitest`)
3. Project modules via the `@/` alias (`import { fmtNum } from '@/lib/format';`) or sibling relative imports (`./FlashPrice`, `./TradingProvider`)

**Path aliases:** `@/` for frontend project root (`tsconfig.json` paths; `vitest.config.mts` alias to the same root).

**Barrel files:** `backend/app/market/__init__.py` re-exports the public surface (`PriceCache`, `MarketDataSource`, `create_market_data_source`, `create_stream_router`, `normalize_ticker`, `Quote`, errors). Import from `app.market`, not from submodules, when crossing the package boundary. Within the package, submodule imports are used.

## Error Handling

**Backend (domain and HTTP):**
- Raise `ApiError(status, code, message)` from `backend/app/errors.py` for anything that should reach the client. Codes are lowercase snake_case: `invalid_quantity`, `invalid_side`, `invalid_ticker`, `unknown_ticker`, `insufficient_cash`, `insufficient_shares`, `quote_unavailable`.
- Domain errors from the market layer (`UnknownTicker`, `QuoteUnavailable`, `NotAuthorized`) are caught and re-raised as `ApiError` with `from exc`:

```python
try:
    await state.source.add_ticker(ticker)
except UnknownTicker as exc:
    raise ApiError(400, "unknown_ticker", str(exc)) from exc
except QuoteUnavailable as exc:
    raise ApiError(503, "quote_unavailable", str(exc)) from exc
```

  (from `_fill_quote` in `backend/app/services/trading.py`)
- `backend/app/main.py` registers `@app.exception_handler(ApiError)` and a `RequestValidationError` handler. Error bodies are always `{"error": {"code": ..., "message": ...}}`.
- Validation of user numbers returns `400` with a specific code rather than letting Pydantic coerce: parse with `Decimal(str(raw))`, check `is_finite()`, and check `quantize` precision (`parse_quantity` in `backend/app/services/trading.py`). Reject `bool` explicitly before numeric checks.
- Money math uses `Decimal`, quantized with `ROUND_HALF_UP` to `CENT` for cash and `QTY_STEP` for quantity; persist as `float`.
- Use `raise ... from None` when the original exception adds nothing (`except InvalidOperation: raise ApiError(...) from None`).
- Database writes that span several statements run inside `with transaction(state.db_path) as conn:` (`backend/app/db.py`) so they roll back on error.
- Background or external failures are logged and swallowed, not raised to the request: `logger.exception("Snapshot failed")` in `backend/app/main.py`.

**Frontend:**
- API and stream errors are caught where the user action happens, and the message is shown in local state: `catch (err) { setError((err as Error).message); }` (`frontend/components/Watchlist.tsx`).
- Malformed SSE payloads are dropped silently: `try { data = JSON.parse(event.data); } catch { return; }` (`frontend/hooks/usePriceStream.ts`).
- Non-async UI callbacks that return promises are wrapped with `void` and a `.catch`: `void removeTicker(ticker).catch((err) => setError(err.message));`.

## Logging

**Backend framework:** standard library `logging`. Each module that logs defines `logger = logging.getLogger(__name__)`. Configured once with `logging.basicConfig(level=logging.INFO)` in `backend/app/main.py`.

**Patterns:**
- Use `%`-style lazy args, never f-strings: `logger.info("Market data source: Massive API (plan=%s)", plan)`.
- `logger.info` for lifecycle (source selected, SSE client connected in `backend/app/market/stream.py`).
- `logger.warning` for expected-but-bad external input (malformed LLM output, LLM call failure in `backend/app/chat.py`).
- `logger.exception` only for unexpected errors where the traceback matters.
- Do not log secrets (API keys) or full LLM prompts.

**Frontend:** no logger; `console` is not used in shipped code. Surface errors in the UI.

## Comments

**When to comment:**
- Explain *why* or a non-obvious contract, not what the line does. Examples in the code: `# E501: handled by formatter; N818: exception names fixed by the interface docs` (`backend/pyproject.toml`), `"""GBM prices can never go negative (exp() is always positive)."""`, `// older timestamps are ignored so charts never receive out-of-order data`.
- Module and public function docstrings are short, one line, and describe the role: `"""Trade execution: the single path for manual and chat trades."""`, `"""Market order at the current price. Caller must hold \`state.lock\`."""`.
- Inline TypeScript comments mark JSX-only intent: `void version; // re-render on every stream update so sparklines read fresh history`.

**Docstrings:** Python uses triple-double-quoted docstrings (one line for simple helpers, multi-line for classes). Tests have docstrings describing the behavior under test (`"""Test that step() returns prices for all tickers."""`).

**JSDoc/TSDoc:** short `/** ... */` one-liners on exported helpers and constants (`/** Append a quote to a chart series: one point per whole second, strictly increasing. */`).

## Function Design

**Size:** keep service functions focused. `execute_trade` delegates to `_fill_quote` and `_apply_trade`, and `reconcile_tracked` is called in `finally`. Components are split by panel (`Watchlist`, `TradeBar`, `PositionsTable`, `Heatmap`, `PnLChart`, `ChatPanel`, `Header`).

**Parameters:**
- Python: positional parameters for required domain input. Dependency injection is through `state` or constructor arguments (`create_app(db_path=..., source=..., cache=..., snapshot_interval=...)`).
- TypeScript: destructured props with inline types for small components: `{ price, text, testId }: { price: number | undefined; text: string; testId?: string }`.
- Test ids are passed as `testId` props and rendered as `data-testid`.

**Return values:**
- Python routes return plain `dict` from FastAPI handlers; Pydantic models are used for request bodies. Typed return annotations are used on service functions (`-> dict`, `-> str`, `-> list[str]`, `-> set[str]`).
- TypeScript: return typed values; formatting helpers return `'—'` for null (`fmtUsd(null) === '—'`).

## Module Design

**Exports:**
- Python: public names are exported from `__init__.py` through explicit imports (and `__all__` where present in `backend/app/market/__init__.py`). Underscore names stay private.
- TypeScript: named exports only (`export function FlashPrice`, `export function Watchlist`, `export const FLASH_MS`). Default exports are not used in components. `app/page.tsx` and `app/layout.tsx` are the only Next.js route files.

**Barrel files:** `backend/app/market/__init__.py` is the public barrel. Frontend has no barrel; import each component from its file.

**Abstractions:**
- Market data uses one abstract base `MarketDataSource` (`backend/app/market/interface.py`) with implementations `GBMSimulator` / simulator source and `MassiveDataSource`. Select at runtime through `create_market_data_source` in `backend/app/market/factory.py`. Do not branch on source type downstream.
- Chat trades and manual trades share one path: `execute_trade()` in `backend/app/services/trading.py`. New trade entry points must call it.

**Shared state:** the single lock `state.lock` (`backend/app/state.py`) serializes trade and watchlist mutations. Hold it when calling `execute_trade`.

---

*Convention analysis: 2026-10-18*
