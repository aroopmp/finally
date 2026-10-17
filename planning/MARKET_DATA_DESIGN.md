# Market Data Backend — Design

Implementation-ready design for the FinAlly market data subsystem: the unified API, the GBM simulator, the Massive client, the SSE stream and the service layer that ties them to the rest of the backend. It targets the **revised contract** in [MARKET_INTERFACE.md](MARKET_INTERFACE.md), [MARKET_SIMULATOR.md](MARKET_SIMULATOR.md) and [MASSIVE_API.md](MASSIVE_API.md), and resolves the market-data findings of [REVIEW.md](REVIEW.md) (items 2, 3, 4, 7 and the market parts of 8–10). It supersedes [archive/MARKET_DATA_DESIGN.md](archive/MARKET_DATA_DESIGN.md), which describes the first implementation.

Everything lives in `backend/app/market/`. **All code in this document was run** (formatted and linted with the backend's ruff settings): it was built as a package, exercised by the test suite in section 14 (57 tests, passing) and the app wiring in section 12 was served with uvicorn and read over SSE with `curl`. The snippets are the files, not sketches, so an implementing agent can copy them verbatim. Where something is a sketch (DB access, the frontend hook) it is labelled.

## Contents

1. [Decisions](#1-decisions)
2. [Architecture](#2-architecture)
3. [Module layout and migration from the current code](#3-module-layout-and-migration-from-the-current-code)
4. [Foundations: errors, config, model, time, tickers](#4-foundations)
5. [PriceCache](#5-pricecache)
6. [The source interface](#6-the-source-interface)
7. [Simulator](#7-simulator)
8. [Massive source](#8-massive-source)
9. [Factory](#9-factory)
10. [MarketService: tracked set, trade and valuation prices](#10-marketservice)
11. [SSE stream](#11-sse-stream)
12. [App wiring and how other components use it](#12-app-wiring-and-usage)
13. [Failure matrix](#13-failure-matrix)
14. [Tests](#14-tests)
15. [Configuration reference](#15-configuration-reference)
16. [Implementation order](#16-implementation-order)
17. [Open items](#17-open-items)

---

## 1. Decisions

The planning docs left several choices open. This design makes them, so downstream agents have one answer.

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | Price type | `Quote` replaces `PriceUpdate`. Carries `prev_price` (tick), `prev_close` (daily baseline), `as_of` (trade time), `fetched_at` (our refresh time), `source`. | The old type conflated tick change with daily change and dropped the baseline (REVIEW 4). |
| 2 | Freshness | `get_fresh()` ages a quote by **`fetched_at`** (is the feed alive?), with a limit per source kind. `as_of` is only for display. | A delayed or after-hours quote has an old `as_of` by design but is still the correct price to trade at. A dead feed is what must block trading (REVIEW 2). |
| 3 | Ticker universe | Format: `^[A-Z]{1,5}$` after trim/uppercase, one function (`normalize_ticker`). **Simulator**: the catalog in `seed_prices.py` (27 symbols, including `PYPL` so the PLAN's chat example works). **Massive**: any well-formed symbol the provider can price; absence from the response means `UnknownTickerError`. | PLAN rejects unknown tickers in both modes. The ten-symbol allowlist (all already on the default watchlist) made "add a ticker" meaningless (REVIEW 7). |
| 4 | Who owns the tracked set | `MarketService.reconcile(watchlist, held)` makes the source track exactly *watchlist ∪ positions*. Sources never decide. | A held ticker must stay priced after leaving the watchlist; a first trade in an untracked ticker must work. |
| 5 | First trade in an untracked ticker | `MarketService.quote_for_trade()` falls back to a one-shot `source.get_quotes()`; the caller reconciles after the trade commits. | Closes the "supported ticker has no quote" gap (REVIEW 2). |
| 6 | Massive plans | `MASSIVE_PLAN` = `realtime` or `delayed` (default `delayed`) labels snapshot quotes. A 403 on the snapshot endpoint switches the process to **EOD mode** (grouped daily bars, last close, labelled `massive-eod`). A 401 sets status `error`. No silent fall back to simulated prices. | The snapshot endpoint is not on the free plan; the API does not label delayed vs real-time per response (MASSIVE_API.md §2, §5). |
| 7 | EOD mode and trading | Allowed at last close; `/api/health` reports `end_of_day: true` so the UI shows a banner. | Simplest honest behaviour (MARKET_INTERFACE.md §9.3). |
| 8 | Timestamps | Unix **seconds** (float) everywhere internally and on the wire. Provider units are converted at the edge with an explicit unit: `to_unix_seconds(ts, "ns" \| "ms" \| "s")`. | The old code divided a nanosecond field by 1000 (REVIEW 3). |
| 9 | SSE payload | Full snapshot of the cache, keyed by ticker, sent on connect, on every cache version change, and `{}` when empty. Idle heartbeat comment. Removal bumps the version. | REVIEW 4. |
| 10 | Simulator determinism | `SIM_SEED=<int>` seeds both RNGs. | Reproducible E2E runs (REVIEW 10). |

## 2. Architecture

```
                    MASSIVE_API_KEY set?  ──no──►  SimulatorDataSource  (GBM, 500 ms ticks)
 create_market_data_source(cache)  │
                                   └─yes──►  MassiveDataSource
                                               ├─ snapshot mode (realtime / delayed plan)
                                               └─ EOD mode      (403 on snapshot: free plan)
                                                        │ writes Quote(s)
                                                        ▼
                                                 PriceCache  (thread-safe, versioned)
                          ┌───────────────────────────┼───────────────────────────┐
                          ▼                           ▼                           ▼
                   SSE stream                 MarketService               MarketService
              GET /api/stream/prices        .quote_for_trade()          .valuation_quote()
              (full snapshot on change)      (trade fill price)      (portfolio value, snapshots)
```

Ownership rules:

- **Sources produce, the cache stores, consumers read.** Nothing outside `app/market/` imports `massive` or the simulator.
- **Two paths.** The *streaming path* (`start/add_ticker/remove_ticker`) keeps the cache current for the tracked set. The *one-shot path* (`get_quotes`, `get_eod_closes`) answers for any tickers, never touches the cache or the tracked set.
- **Threading.** The simulator and the pollers run on the event loop; the Massive SDK is synchronous and runs in `asyncio.to_thread`, so the cache uses `threading.Lock`. The tracked-ticker set is only touched on the event loop; worker threads receive a copy.
- **Failures degrade, they do not crash.** Background loops never raise; they set `status` and keep serving older quotes, which age out of `get_fresh`.

## 3. Module layout and migration from the current code

The current `backend/app/market/` implements the first contract (`PriceUpdate`). Target layout, with what changes:

| File | State | Change |
|---|---|---|
| `models.py` | rewrite | `PriceUpdate` → `Quote`, adds `SourceKind`, `SourceStatus`. Field renames: `previous_price`→`prev_price`, `timestamp`→`as_of`; `change`/`change_percent` removed in favour of `day_change`/`day_change_pct` (vs `prev_close`) and `direction` (vs previous tick). |
| `errors.py` | new | `InvalidTickerError`, `UnknownTickerError`, `QuoteUnavailableError`. |
| `config.py` | new | All intervals, limits and freshness constants. |
| `timeutil.py` | new | Explicit-unit timestamp conversion. |
| `tickers.py` | new | `normalize_ticker()`; one validator for manual, chat and API paths. |
| `cache.py` | rewrite | `update(..., source=, as_of=, prev_close=)` validates prices, carries `prev_close` forward, `remove()` bumps the version, adds `get_fresh`, `set_prev_close`, `clear`, `snapshot`. Constructor takes an injectable clock. |
| `interface.py` | extend | Adds `kind`, `status`, `get_quotes`, `get_eod_closes`, `reset`, `fresh_limit`. |
| `seed_prices.py` | extend | Catalog grows from 10 to 27 tickers; groups extended. |
| `simulator.py` | modify | Seedable RNG, `dt` derived from the interval, baseline `prev_close`, `get_quotes/get_eod_closes/reset`, unknown tickers rejected instead of random-priced. |
| `massive_client.py` | rewrite | Correct SDK attributes and units, price-tier fallback, plan detection, EOD mode, error classification, add-with-first-quote, backoff. |
| `factory.py` | modify | Reads `MASSIVE_PLAN`, `SIM_SEED`. Imports stay top-level (`massive` is a core dependency). |
| `stream.py` | modify | Router created per call (no module-level router), testable generator, initial snapshot, `{}` when empty, heartbeat, atomic version+snapshot read. |
| `service.py` | new | `MarketService`. |
| `__init__.py` | modify | New exports. |

Other files that change when this lands:

- `backend/market_data_demo.py`: uses `change_percent`, `price_cache=`. Switch to `day_change_pct` / `direction` and the positional `cache` argument.
- `backend/tests/market/test_models.py`, `test_cache.py`, `test_massive.py`, `test_simulator*.py`, `test_factory.py`: update to the new types; section 14 supplies replacements for the cache, model, simulator, Massive and stream tests. Keep `test_factory.py`'s cases (unset, empty and whitespace key → simulator; non-empty → Massive).
- `backend/CLAUDE.md` and `planning/MARKET_DATA_SUMMARY.md` describe the old API; update them in the same change.

## 4. Foundations

### 4.1 Errors

Three exceptions, each with one HTTP meaning. `app.main.http_error()` (section 12) is the single mapping.

**`backend/app/market/errors.py`**

```python
"""Exceptions raised by the market data layer."""

from __future__ import annotations


class MarketDataError(Exception):
    """Base class for market data failures."""


class InvalidTickerError(MarketDataError):
    """The symbol is not 1-5 letters A-Z. Maps to HTTP 400."""


class UnknownTickerError(MarketDataError):
    """Well-formed symbol that the active source cannot price. Maps to HTTP 400."""


class QuoteUnavailableError(MarketDataError):
    """A symbol the source should be able to price has no usable quote right now.

    Missing, stale, or the provider is failing. Maps to HTTP 503 (retryable).
    """
```

### 4.2 Configuration constants

**`backend/app/market/config.py`**

```python
"""Every tunable number of the market data layer lives here."""

from __future__ import annotations

import math

from .models import SourceKind

# --- Simulator ---------------------------------------------------------------
SIM_UPDATE_INTERVAL_S = 0.5
SIM_EVENT_PROBABILITY = 0.001  # per ticker per tick; 0 in deterministic tests
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600

# --- Massive -----------------------------------------------------------------
MASSIVE_POLL_INTERVAL_S = {"realtime": 3.0, "delayed": 15.0}
MASSIVE_BACKOFF_MAX_S = 120.0  # cap for exponential backoff after failed polls
MASSIVE_CHUNK_SIZE = 250  # tickers per snapshot request (keeps URLs short)
EOD_REFRESH_S = 6 * 3600  # re-read grouped daily bars this often
EOD_RETRY_S = 60.0  # after a failed EOD fetch
EOD_LOOKBACK_DAYS = 7  # weekend/holiday walk-back limit

# --- Freshness ---------------------------------------------------------------
# Age is measured from Quote.fetched_at (feed liveness), NOT Quote.as_of. A delayed or
# after-hours quote has an old as_of by design; what matters for trading is that our
# feed is still alive and refreshed the quote recently.
FRESH_LIMIT_S: dict[SourceKind, float] = {
    SourceKind.SIMULATOR: 5.0,
    SourceKind.MASSIVE_REALTIME: 30.0,
    SourceKind.MASSIVE_DELAYED: 75.0,  # 5x the 15 s poll interval
    SourceKind.MASSIVE_EOD: math.inf,  # last close does not expire within the session
}
```

Freshness is judged on `fetched_at`, so: a simulator quote is stale 5 s after the loop stops writing; a 3-second realtime poll tolerates ten missed cycles' worth of slack (30 s); a 15-second delayed poll tolerates five (75 s); EOD quotes never expire inside the session because the close is the price.

### 4.3 Model

**`backend/app/market/models.py`**

```python
"""Data model: the one type that leaves the market data layer."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum


class SourceKind(str, Enum):
    SIMULATOR = "simulator"
    MASSIVE_REALTIME = "massive-realtime"
    MASSIVE_DELAYED = "massive-delayed"  # ~15 minutes old
    MASSIVE_EOD = "massive-eod"  # last close only (free plan, or no live print yet)


class SourceStatus(str, Enum):
    STARTING = "starting"  # no successful fetch yet
    OK = "ok"
    DEGRADED = "degraded"  # last fetch failed; serving older quotes
    ERROR = "error"  # unrecoverable, e.g. bad API key


@dataclass(frozen=True, slots=True)
class Quote:
    ticker: str
    price: float  # positive, finite
    prev_price: float  # previous cached price; drives the green/red flash
    prev_close: float | None  # daily-change baseline; None if unknown
    as_of: float  # unix SECONDS the price was struck (trade time)
    fetched_at: float  # unix seconds we stored it (feed liveness)
    source: SourceKind

    @property
    def direction(self) -> str:
        if self.price > self.prev_price:
            return "up"
        if self.price < self.prev_price:
            return "down"
        return "flat"

    @property
    def day_change(self) -> float | None:
        return None if self.prev_close is None else round(self.price - self.prev_close, 4)

    @property
    def day_change_pct(self) -> float | None:
        if not self.prev_close:
            return None
        return round((self.price - self.prev_close) / self.prev_close * 100, 4)

    def age(self, now: float | None = None) -> float:
        """Seconds since we last refreshed this quote."""
        return (time.time() if now is None else now) - self.fetched_at

    def to_dict(self) -> dict:
        """SSE / REST representation. All times are unix seconds."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "prev_price": self.prev_price,
            "prev_close": self.prev_close,
            "day_change": self.day_change,
            "day_change_pct": self.day_change_pct,
            "direction": self.direction,
            "as_of": self.as_of,
            "source": self.source.value,
        }
```

Two percentages exist on purpose:

| Field | Baseline | Used by |
|---|---|---|
| `direction` | previous cached price (`prev_price`) | green/red flash |
| `day_change_pct` | `prev_close` | watchlist "Chg %" column |

`prev_close` means: simulator → the seed price (the UI labels it "since start"); Massive snapshot → `prev_day.close`; Massive EOD → the prior session's close. `None` when unknown, which the UI renders as "–", never as 0.

Example, simulator, a few seconds after start:

```python
Quote(ticker="AAPL", price=190.12, prev_price=190.09, prev_close=190.0,
      as_of=1760649600.5, fetched_at=1760649600.5, source=SourceKind.SIMULATOR)
.direction == "up"; .day_change == 0.12; .day_change_pct == 0.0632
```

### 4.4 Timestamps and tickers

Massive mixes units: `last_trade.sip_timestamp` and `updated` are **nanoseconds**, `min.timestamp` and aggregate `timestamp` are **milliseconds**. There is deliberately no magnitude-guessing helper; every call site names its unit.

**`backend/app/market/timeutil.py`**

```python
"""Timestamp normalisation. Massive mixes units, so callers name the unit explicitly."""

from __future__ import annotations

import math
from typing import Literal

Unit = Literal["ns", "ms", "s"]
_DIVISOR = {"ns": 1e9, "ms": 1e3, "s": 1.0}


def to_unix_seconds(ts: int | float | None, unit: Unit) -> float | None:
    """Convert a provider timestamp to unix seconds; None for missing/zero/garbage."""
    if ts is None or isinstance(ts, bool):
        return None
    try:
        if not math.isfinite(ts) or ts <= 0:
            return None
    except TypeError:
        return None
    return ts / _DIVISOR[unit]
```

**`backend/app/market/tickers.py`**

```python
"""Ticker normalisation: the single place symbol format is decided."""

from __future__ import annotations

import re

from .errors import InvalidTickerError

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


def normalize_ticker(raw: object) -> str:
    """Trim, uppercase, and validate format. Every input path calls this first."""
    if not isinstance(raw, str):
        raise InvalidTickerError(f"ticker must be a string, got {type(raw).__name__}")
    ticker = raw.strip().upper()
    if not _TICKER_RE.match(ticker):
        raise InvalidTickerError(f"invalid ticker {raw!r}: expected 1-5 letters A-Z")
    return ticker
```

## 5. PriceCache

Behaviour worth knowing before reading the code:

- `update()` **rejects** `None`, `NaN`, infinite, non-positive and boolean prices: it logs, keeps the old quote and returns `None`. A missing price can never become `0` downstream.
- `prev_close` is stored per ticker and **carried forward** on every update; a source passes it once (or whenever the baseline changes).
- `version` moves only when something a client can *see* changed (`price`, `prev_price`, `prev_close`, `as_of`, `source`). A Massive re-poll that returns the same trade renews `fetched_at` (freshness) without re-sending an SSE event. `remove()` and `clear()` bump it, so streams can propagate removals.
- `snapshot()` returns `(version, quotes)` under one lock, so the SSE stream never pairs a version with a different quote set.
- `get_fresh(ticker)` defaults the age limit to the quote's own source; trading and valuation use it, never bare `get()`.

**`backend/app/market/cache.py`**

```python
"""Thread-safe in-memory quote store shared by producers and consumers."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from threading import Lock

from .config import FRESH_LIMIT_S
from .models import Quote, SourceKind

logger = logging.getLogger(__name__)


class PriceCache:
    """Latest Quote per ticker.

    Writers: the active MarketDataSource (event-loop task, plus worker threads for Massive).
    Readers: the SSE stream, portfolio valuation, trade execution.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._quotes: dict[str, Quote] = {}
        self._prev_close: dict[str, float] = {}  # survives updates; dropped on remove()
        self._lock = Lock()
        self._version = 0
        self._clock = clock

    # --- writes ----------------------------------------------------------------
    def update(
        self,
        ticker: str,
        price: float,
        *,
        source: SourceKind,
        as_of: float | None = None,
        prev_close: float | None = None,
    ) -> Quote | None:
        """Store a new price. Returns the Quote, or None if the price was rejected.

        A rejected price (None, NaN, infinite, <= 0) is logged and the old quote is kept.
        The version only moves when something a client can see changed.
        """
        if not _valid_price(price):
            logger.warning("Rejected invalid price for %s: %r", ticker, price)
            return None
        price = round(float(price), 4)
        now = self._clock()
        with self._lock:
            old = self._quotes.get(ticker)
            if prev_close is not None and _valid_price(prev_close):
                self._prev_close[ticker] = float(prev_close)
            quote = Quote(
                ticker=ticker,
                price=price,
                prev_price=old.price if old else price,
                prev_close=self._prev_close.get(ticker),
                as_of=as_of if as_of is not None else now,
                fetched_at=now,
                source=source,
            )
            self._quotes[ticker] = quote
            if old is None or _visible(old) != _visible(quote):
                self._version += 1
            return quote

    def set_prev_close(self, ticker: str, prev_close: float) -> None:
        """Set the daily-change baseline. Applies to the existing quote immediately."""
        if not _valid_price(prev_close):
            return
        with self._lock:
            self._prev_close[ticker] = float(prev_close)
            q = self._quotes.get(ticker)
            if q is not None:
                self._quotes[ticker] = Quote(
                    q.ticker,
                    q.price,
                    q.prev_price,
                    float(prev_close),
                    q.as_of,
                    q.fetched_at,
                    q.source,
                )
                self._version += 1

    def remove(self, ticker: str) -> None:
        with self._lock:
            self._prev_close.pop(ticker, None)
            if self._quotes.pop(ticker, None) is not None:
                self._version += 1

    def clear(self) -> None:
        with self._lock:
            had = bool(self._quotes)
            self._quotes.clear()
            self._prev_close.clear()
            if had:
                self._version += 1

    # --- reads -----------------------------------------------------------------
    def get(self, ticker: str) -> Quote | None:
        with self._lock:
            return self._quotes.get(ticker)

    def get_fresh(self, ticker: str, max_age: float | None = None) -> Quote | None:
        """The quote, or None if missing or not refreshed recently enough.

        `max_age` defaults to the limit for the quote's own source (config.FRESH_LIMIT_S).
        Trading and valuation use this, never bare get().
        """
        q = self.get(ticker)
        if q is None:
            return None
        limit = FRESH_LIMIT_S[q.source] if max_age is None else max_age
        return q if q.age(self._clock()) <= limit else None

    def get_all(self) -> dict[str, Quote]:
        with self._lock:
            return dict(self._quotes)

    def snapshot(self) -> tuple[int, dict[str, Quote]]:
        """Version and quotes read together, so a stream never pairs them inconsistently."""
        with self._lock:
            return self._version, dict(self._quotes)

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._quotes)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._quotes


def _valid_price(p: object) -> bool:
    return isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and p > 0


def _visible(q: Quote) -> tuple:
    """Everything a client sees. fetched_at is deliberately excluded."""
    return (q.price, q.prev_price, q.prev_close, q.as_of, q.source)
```

## 6. The source interface

**`backend/app/market/interface.py`**

```python
"""The contract every market data provider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from .config import FRESH_LIMIT_S
from .models import Quote, SourceKind, SourceStatus


class MarketDataSource(ABC):
    """Pushes quotes into a shared PriceCache on its own schedule.

    Streaming path: start() / add_ticker() / remove_ticker() feed the cache.
    One-shot path: get_quotes() / get_eod_closes() answer for any tickers without
    touching the cache or the tracked set.

    Rules every implementation follows:
      * add_ticker returns only once the ticker has a cache entry, or raises
        UnknownTickerError / QuoteUnavailableError.
      * Provider and network failures never escape the background loop; they set
        `status = DEGRADED` (or ERROR for unrecoverable) and log.
      * The source does not decide *which* tickers to track. The caller owns the
        tracked set (watchlist union open positions); see MarketService.
    """

    kind: SourceKind
    status: SourceStatus

    @property
    def fresh_limit(self) -> float:
        """Max quote age (seconds since fetch) acceptable for trading and valuation."""
        return FRESH_LIMIT_S[self.kind]

    # --- streaming path ----------------------------------------------------------
    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing quotes. Call once; the first fetch completes before returning."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop background work. Idempotent. No cache writes afterwards."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Track a ticker. No-op if tracked. Raises UnknownTickerError / QuoteUnavailableError."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking and drop its cache entry. No-op if not tracked."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers."""

    async def reset(self) -> None:
        """Return to initial market state (used by POST /api/reset). Default: nothing."""

    # --- one-shot path -------------------------------------------------------------
    @abstractmethod
    async def get_quotes(self, tickers: list[str]) -> dict[str, Quote]:
        """Latest quote per ticker; unpriceable tickers are omitted, never zero.

        Does not write to the cache and does not change the tracked set.
        """

    @abstractmethod
    async def get_eod_closes(self, tickers: list[str], on: date | None = None) -> dict[str, float]:
        """Close for `on` (default: most recent completed session); omits unknowns."""
```

Contract details that implementations and tests rely on:

| Method | Contract |
|---|---|
| `start(tickers)` | Called once. The first fetch completes before it returns (so the first SSE event has data). Provider failure does **not** raise; it sets `status`. |
| `add_ticker(t)` | Normalises, then returns only when `t` has a cache entry. Raises `InvalidTickerError`, `UnknownTickerError` (cannot be priced) or `QuoteUnavailableError` (provider failing). |
| `remove_ticker(t)` | No-op if untracked; drops the cache entry. |
| `get_quotes(ts)` | Latest quote for each; unpriceable tickers **omitted**. Provider failure raises `QuoteUnavailableError`, which is different from "not found". No cache writes. |
| `get_eod_closes(ts, on)` | Close for session `on`, default the last completed session. Simulator returns its baseline and ignores `on`. |
| `reset()` | Simulator restarts from seeds (used by `POST /api/reset`); Massive no-op. |

## 7. Simulator

The model, per ticker and per tick:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z ),     Z = L·z,   z ~ N(0, I),   C = L·Lᵀ
dt = update_interval / (252 · 6.5 · 3600)   ≈ 8.5e-8 for 500 ms
```

- `C` is the correlation matrix from `seed_prices.py` (tech 0.6, finance 0.5, TSLA and cross-sector 0.3). Off-diagonals in (0, 1) with a unit diagonal make `C` positive definite, so `cholesky` succeeds for every catalog subset (tested for 1, 2, 10 and 27 tickers). It is rebuilt on add/remove.
- Each ticker has a 0.1 % chance per tick of a uniform 2–5 % shock, random sign. About one event every 50 s on a 10-ticker board at 2 ticks/s.
- Internal state keeps full float precision; only the value written to the cache is rounded to cents, so tiny moves accumulate rather than vanish.
- With σ = 0.25, one tick has a standard deviation of about 1.4 cents on $190, and a full 6.5-hour session about 1.6 %.
- `dt` is computed from `update_interval`; changing the interval can no longer silently change the effective volatility.
- `prev_close` = the seed. The simulator starts from the same prices on every process start, so P&L from a previous run is measured against reset prices (acceptable for a demo; the UI help should say so).
- Unknown tickers are rejected (`UnknownTickerError`) rather than given a random price, per decision 3. At `start()` an unknown ticker in persisted state is skipped with a warning instead of failing the boot.

### 7.1 Catalog

**`backend/app/market/seed_prices.py`**

```python
"""Simulator catalog: seed prices, GBM parameters, correlation structure.

The keys of SEED_PRICES are the simulator's ticker universe. A symbol outside it is
rejected (UnknownTickerError) in simulator mode. Prices are illustrative round numbers, not quotes.
"""

# Default watchlist (the first ten) plus a curated extension so "add PYPL" works.
SEED_PRICES: dict[str, float] = {
    # --- default watchlist ---
    "AAPL": 190.00,
    "GOOGL": 175.00,
    "MSFT": 420.00,
    "AMZN": 185.00,
    "TSLA": 250.00,
    "NVDA": 800.00,
    "META": 500.00,
    "JPM": 195.00,
    "V": 280.00,
    "NFLX": 600.00,
    # --- extension ---
    "AMD": 160.00,
    "INTC": 32.00,
    "ORCL": 125.00,
    "CRM": 270.00,
    "ADBE": 520.00,
    "UBER": 75.00,
    "PYPL": 65.00,
    "DIS": 110.00,
    "BA": 180.00,
    "KO": 62.00,
    "PEP": 170.00,
    "WMT": 68.00,
    "XOM": 115.00,
    "COST": 780.00,
    "BAC": 38.00,
    "GS": 450.00,
    "MA": 460.00,
}

# sigma: annualised volatility; mu: annualised drift.
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},  # high volatility
    "NVDA": {"sigma": 0.40, "mu": 0.08},  # high volatility, strong drift
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},
    "V": {"sigma": 0.17, "mu": 0.04},
    "NFLX": {"sigma": 0.35, "mu": 0.05},
    "AMD": {"sigma": 0.45, "mu": 0.06},
    "INTC": {"sigma": 0.35, "mu": 0.02},
    "UBER": {"sigma": 0.38, "mu": 0.05},
    "PYPL": {"sigma": 0.33, "mu": 0.03},
}
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}  # catalog tickers without an entry

CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {
        "AAPL",
        "GOOGL",
        "MSFT",
        "AMZN",
        "META",
        "NVDA",
        "NFLX",
        "AMD",
        "INTC",
        "ORCL",
        "CRM",
        "ADBE",
        "UBER",
    },
    "finance": {"JPM", "V", "MA", "BAC", "GS", "PYPL"},
}
INTRA_TECH_CORR = 0.6
INTRA_FINANCE_CORR = 0.5
CROSS_GROUP_CORR = 0.3  # across sectors, and for tickers in no group
TSLA_CORR = 0.3  # TSLA does its own thing
```

### 7.2 Engine and source

`GBMSimulator` is pure and synchronous (tests call `step()` with a seed). `SimulatorDataSource` adds the asyncio loop, cache writes and the source contract.

**`backend/app/market/simulator.py`**

```python
"""GBM market simulator: the default source when MASSIVE_API_KEY is unset."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections.abc import Callable
from datetime import date

import numpy as np

from .cache import PriceCache
from .config import SIM_EVENT_PROBABILITY, SIM_UPDATE_INTERVAL_S, TRADING_SECONDS_PER_YEAR
from .errors import UnknownTickerError
from .interface import MarketDataSource
from .models import Quote, SourceKind, SourceStatus
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)


def dt_for_interval(interval_s: float) -> float:
    """One tick as a fraction of a trading year. Keeps sigma honest if the interval changes."""
    return interval_s / TRADING_SECONDS_PER_YEAR


class GBMSimulator:
    """Pure math: correlated geometric Brownian motion plus random shock events.

        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z),   Z = L @ z

    No asyncio, no cache, no clock. Tests call step() directly with a seed.
    """

    def __init__(
        self,
        tickers: list[str],
        dt: float = dt_for_interval(SIM_UPDATE_INTERVAL_S),
        event_probability: float = SIM_EVENT_PROBABILITY,
        seed: int | None = None,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        self._np_rng = np.random.default_rng(seed)
        self._rng = random.Random(seed)
        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None
        for t in tickers:
            self._add_internal(t)
        self._rebuild_cholesky()

    def step(self) -> dict[str, float]:
        """Advance every ticker one tick; returns {ticker: price rounded to cents}."""
        n = len(self._tickers)
        if n == 0:
            return {}
        z = self._np_rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z
        out: dict[str, float] = {}
        for i, t in enumerate(self._tickers):
            mu, sigma = self._params[t]["mu"], self._params[t]["sigma"]
            self._prices[t] *= math.exp(
                (mu - 0.5 * sigma**2) * self._dt + sigma * math.sqrt(self._dt) * z[i]
            )
            if self._rng.random() < self._event_prob:
                magnitude = self._rng.uniform(0.02, 0.05)
                self._prices[t] *= 1 + magnitude * self._rng.choice((-1, 1))
            out[t] = round(self._prices[t], 2)  # internal state keeps full precision
        return out

    def add_ticker(self, ticker: str) -> None:
        if ticker not in self._prices:
            self._add_internal(ticker)
            self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        if ticker in self._prices:
            self._tickers.remove(ticker)
            del self._prices[ticker], self._params[ticker]
            self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- internals -------------------------------------------------------------
    def _add_internal(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        if ticker not in SEED_PRICES:
            raise UnknownTickerError(ticker)
        self._tickers.append(ticker)
        self._prices[ticker] = SEED_PRICES[ticker]
        self._params[ticker] = TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)

    def _rebuild_cholesky(self) -> None:
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                corr[i, j] = corr[j, i] = self._pairwise_correlation(
                    self._tickers[i], self._tickers[j]
                )
        # All off-diagonals are in (0, 1) with a unit diagonal: positive definite.
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        if "TSLA" in (t1, t2):
            return TSLA_CORR
        tech, fin = CORRELATION_GROUPS["tech"], CORRELATION_GROUPS["finance"]
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in fin and t2 in fin:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """Runs GBMSimulator.step() every `update_interval` seconds and writes to the cache.

    `prev_close` is the seed price: the simulator has no history, so "daily change" means
    "change since the simulation started" (the UI labels it that way).
    """

    kind = SourceKind.SIMULATOR

    def __init__(
        self,
        cache: PriceCache,
        update_interval: float = SIM_UPDATE_INTERVAL_S,
        event_probability: float = SIM_EVENT_PROBABILITY,
        seed: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cache = cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._seed = seed
        self._clock = clock
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None
        self.status = SourceStatus.STARTING

    # --- lifecycle ---------------------------------------------------------------
    async def start(self, tickers: list[str]) -> None:
        known = [t for t in dict.fromkeys(tickers) if t in SEED_PRICES]
        for t in set(tickers) - set(known):
            logger.warning("Simulator: skipping unknown ticker %s at start", t)
        self._sim = GBMSimulator(
            known,
            dt=dt_for_interval(self._interval),
            event_probability=self._event_prob,
            seed=self._seed,
        )
        for t in known:
            self._seed_cache(t)  # first SSE event is full; the watchlist is never blank
        self.status = SourceStatus.OK
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started: %d tickers", len(known))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in SEED_PRICES:
            raise UnknownTickerError(ticker)
        assert self._sim is not None, "start() first"
        if ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._seed_cache(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.strip().upper()
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    async def reset(self) -> None:
        """Restart every tracked ticker from its seed; prev_close resets with it."""
        tickers = self.get_tickers()
        self._sim = GBMSimulator(
            tickers,
            dt=dt_for_interval(self._interval),
            event_probability=self._event_prob,
            seed=self._seed,
        )
        for t in tickers:
            self._seed_cache(t)

    # --- one-shot ----------------------------------------------------------------
    async def get_quotes(self, tickers: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        now = self._clock()
        for raw in tickers:
            t = raw.strip().upper()
            q = self._cache.get(t)
            if q is None and t in SEED_PRICES:  # untracked but known: seed, no state kept
                p = SEED_PRICES[t]
                q = Quote(t, p, p, p, now, now, SourceKind.SIMULATOR)
            if q is not None:
                out[t] = q
        return out

    async def get_eod_closes(self, tickers: list[str], on: date | None = None) -> dict[str, float]:
        """Not history: returns the simulation's baseline (the seed). `on` is ignored."""
        return {t: SEED_PRICES[t] for t in (x.strip().upper() for x in tickers) if t in SEED_PRICES}

    # --- internals -----------------------------------------------------------------
    def _seed_cache(self, ticker: str) -> None:
        assert self._sim is not None
        price = self._sim.get_price(ticker)
        self._cache.update(
            ticker,
            price,
            source=SourceKind.SIMULATOR,
            as_of=self._clock(),
            prev_close=SEED_PRICES[ticker],
        )

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    now = self._clock()
                    for t, price in self._sim.step().items():
                        self._cache.update(t, price, source=SourceKind.SIMULATOR, as_of=now)
            except Exception:
                logger.exception("Simulator step failed")  # one bad tick must not kill the feed
            await asyncio.sleep(self._interval)
```

Tuning knobs and deterministic mode:

| Knob | Where | Effect |
|---|---|---|
| `update_interval` | constructor, default 0.5 s | tick rate; `dt` follows it |
| `event_probability` | constructor / `config.SIM_EVENT_PROBABILITY` | 0 disables shocks (tests) |
| `seed` / `SIM_SEED` | constructor / env | same seed → identical price paths |
| `TICKER_PARAMS` | `seed_prices.py` | per-ticker σ, μ |

Even with `SIM_SEED` the walk is not a fixture: E2E tests that assert exact cash or P&L should still pin a quote through a test hook (`cache.update(..., as_of=...)`) rather than depend on the path.

## 8. Massive source

### 8.1 What the provider gives us

Summarised from [MASSIVE_API.md](MASSIVE_API.md) and checked against the installed SDK (`massive==2.8.0`) by deserialising sample JSON through the real models:

| Need | Endpoint / SDK call | Plan | Notes |
|---|---|---|---|
| Live or delayed prices, many tickers, one call | `client.get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=[...])` → `list[TickerSnapshot]` | Starter+ (15-min delayed), Advanced (real-time). **Not on the free plan.** | The SDK joins the list into a comma-separated query param. |
| Last close for every stock | `client.get_grouped_daily_aggs(date, adjusted=True)` → `list[GroupedDailyAgg]` | Free and up | The only multi-ticker price call on the free plan. Large response: call rarely. |

`TickerSnapshot` fields we read (SDK attribute → unit):

| Attribute | Unit | Use |
|---|---|---|
| `last_trade.price`, `last_trade.sip_timestamp` | $, **ns** | best price and its time (`participant_timestamp` as fallback) |
| `min.close`, `min.timestamp` | $, **ms** | fallback 1 |
| `day.close`, `updated` | $, **ns** | fallback 2 |
| `prev_day.close`, `prev_day.timestamp` | $, **ms** | `prev_close`; also fallback 3 (stale) |

`LastTrade` has **no** `.timestamp` attribute (the first implementation read it and divided by 1000). The SDK raises `BadResponse(<body text>)` for any non-200 and does **not** expose the HTTP status, so error handling reads the JSON body (8.3).

### 8.2 Modes and behaviour

| Situation | Behaviour |
|---|---|
| Snapshot works, `MASSIVE_PLAN=realtime` | Poll every 3 s. Quotes tagged `massive-realtime`. |
| Snapshot works, `MASSIVE_PLAN=delayed` (default) | Poll every 15 s. Tagged `massive-delayed`. |
| Snapshot returns "not authorized" (403) | Switch **once** to EOD mode for the process lifetime. Grouped daily bars for the last two completed sessions: price = latest close, `prev_close` = the session before. Tagged `massive-eod`. Refresh every 6 h. |
| Key rejected (401) | `status = error`. Cache stays empty. **Not** replaced by simulated prices. Keeps retrying with backoff (capped at 120 s); the normal fix is correcting `.env` and restarting. |
| Poll fails after earlier success (timeout, 429, 5xx) | `status = degraded`; old quotes stay in the cache but stop being trade-eligible once `fetched_at` exceeds the limit; exponential backoff from the poll interval to 120 s; recovers to `ok` on the next success. |
| Snapshot lacks a trade (pre-market, plan without trades) | Falls through `min.close` → `day.close` → `prev_day.close`. The last tier is tagged `massive-eod` **per quote**, so a stale-by-construction price is never labelled live. |
| Ticker missing from the response | Left unchanged; `add_ticker` of such a ticker raises `UnknownTickerError`. |
| Ticker removed while a request is in flight | The response is ignored for that ticker (no resurrection in the cache). |

Tick direction: Massive prices change only when a new trade prints, so `prev_price` is the previously cached price and an unchanged price is `flat`. Movement is never synthesised.

Example, one snapshot (JSON as the API returns it) and the `Quote` it produces:

```json
{"ticker": "AAPL",
 "lastTrade": {"p": 190.5, "t": 1760649600000000000},
 "min": {"c": 190.4, "t": 1760649540000},
 "day": {"c": 190.3},
 "prevDay": {"c": 189.0},
 "updated": 1760649600000000000}
```

```python
Quote(ticker="AAPL", price=190.5, prev_price=190.5,   # prev_price = previously cached price
      prev_close=189.0, as_of=1760649600.0, fetched_at=<now>,
      source=SourceKind.MASSIVE_REALTIME)
# .day_change == 1.5, .day_change_pct == 0.7937, .direction == "flat" on first sight
```

### 8.3 Code

`classify_error` is the one function to correct once someone runs it against a real key (see open items); everything else only depends on its four outcomes.

**`backend/app/market/massive_client.py`**

```python
"""Massive (formerly Polygon.io) source: snapshot poller with an end-of-day fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal, NamedTuple

from massive import RESTClient
from massive.exceptions import AuthError
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .config import (
    EOD_LOOKBACK_DAYS,
    EOD_REFRESH_S,
    EOD_RETRY_S,
    MASSIVE_BACKOFF_MAX_S,
    MASSIVE_CHUNK_SIZE,
    MASSIVE_POLL_INTERVAL_S,
)
from .errors import QuoteUnavailableError, UnknownTickerError
from .interface import MarketDataSource
from .models import Quote, SourceKind, SourceStatus
from .tickers import normalize_ticker
from .timeutil import to_unix_seconds

logger = logging.getLogger(__name__)

ErrorClass = Literal["auth", "forbidden", "rate_limited", "other"]
PriceTier = Literal["trade", "minute", "day", "prev_close"]


class Mode(str, Enum):
    SNAPSHOT = "snapshot"  # /v2/snapshot: live or 15-min delayed, depending on plan
    EOD = "eod"  # grouped daily bars: last close only (free plan)


# --- pure helpers (unit-tested without any network) ---------------------------------


def _valid(p: object) -> bool:
    return isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and p > 0


@dataclass(frozen=True, slots=True)
class Parsed:
    price: float
    as_of: float
    prev_close: float | None
    tier: PriceTier


def pick_price(snap: Any, now: float) -> Parsed | None:
    """Choose a price from a TickerSnapshot, best source first.

    last_trade.price  ->  min.close  ->  day.close  ->  prev_day.close (stale: tier "prev_close")
    Units differ per field: last_trade/updated are nanoseconds, min/aggregate are milliseconds.
    """
    prev_close = snap.prev_day.close if snap.prev_day and _valid(snap.prev_day.close) else None
    lt, mn, day = snap.last_trade, snap.min, snap.day
    if lt and _valid(lt.price):
        as_of = (
            to_unix_seconds(lt.sip_timestamp, "ns")
            or to_unix_seconds(lt.participant_timestamp, "ns")
            or now
        )
        return Parsed(lt.price, as_of, prev_close, "trade")
    if mn and _valid(mn.close):
        return Parsed(mn.close, to_unix_seconds(mn.timestamp, "ms") or now, prev_close, "minute")
    if day and _valid(day.close):
        return Parsed(day.close, to_unix_seconds(snap.updated, "ns") or now, prev_close, "day")
    if prev_close is not None:
        as_of = to_unix_seconds(snap.prev_day.timestamp, "ms") or now
        return Parsed(prev_close, as_of, None, "prev_close")
    return None


def classify_error(exc: BaseException) -> ErrorClass:
    """Map an SDK exception to what we should do about it.

    The SDK raises BadResponse(<response body text>) for any non-200, and does NOT expose the
    HTTP status, so we read the JSON body. The exact bodies below are unverified against a live
    account (see "Open items"); this function is the single place to fix when that is done.
    """
    if isinstance(exc, AuthError):
        return "auth"
    text = str(exc)
    status = message = ""
    try:
        body = json.loads(text)
        status = str(body.get("status", "")).upper()
        message = str(body.get("message") or body.get("error") or "")
    except (ValueError, AttributeError):
        message = text
    low = f"{status} {message}".lower()
    if "not_authorized" in low or "not entitled" in low or "upgrade your plan" in low:
        return "forbidden"
    if "unknown api key" in low or "invalid api key" in low or "unauthorized" in low:
        return "auth"
    if "maximum requests" in low or "rate limit" in low or "too many requests" in low:
        return "rate_limited"
    return "other"


class Bar(NamedTuple):
    close: float
    as_of: float


def grouped_bars(client: Any, on: date) -> dict[str, Bar]:
    """One call: every US stock's daily bar for `on`. Empty on weekends/holidays."""
    out: dict[str, Bar] = {}
    for b in client.get_grouped_daily_aggs(on, adjusted=True):
        if b.ticker and _valid(b.close):
            as_of = (
                to_unix_seconds(b.timestamp, "ms")
                or datetime.combine(on, datetime.min.time(), tzinfo=timezone.utc).timestamp()
            )
            out[b.ticker] = Bar(float(b.close), as_of)
    return out


@dataclass(frozen=True, slots=True)
class EodData:
    latest_date: date
    latest: dict[str, Bar]
    prior: dict[str, Bar]
    fetched_at: float


def fetch_eod_sessions(client: Any, today: date, clock: Callable[[], float]) -> EodData:
    """The two most recent completed sessions. Weekends skipped locally; holidays cost a call."""
    found: list[tuple[date, dict[str, Bar]]] = []
    d = today
    for _ in range(EOD_LOOKBACK_DAYS):
        d -= timedelta(days=1)
        if d.weekday() >= 5:
            continue
        bars = grouped_bars(client, d)
        if bars:
            found.append((d, bars))
            if len(found) == 2:
                break
    if not found:
        raise LookupError(f"no trading session in the {EOD_LOOKBACK_DAYS} days before {today}")
    return EodData(found[0][0], found[0][1], found[1][1] if len(found) > 1 else {}, clock())


class _NotEntitledError(Exception):
    """Internal: the plan lacks the snapshot endpoint (HTTP 403)."""


# --- the source ---------------------------------------------------------------------


class MassiveDataSource(MarketDataSource):
    """Polls Massive for the tracked tickers and writes Quotes to the cache.

    Capability detection happens on the first snapshot call: a 403 switches the process to EOD
    mode permanently (no retry every cycle). A 401 sets status ERROR and is never papered over
    with simulated prices.
    """

    def __init__(
        self,
        api_key: str,
        cache: PriceCache,
        *,
        plan: Literal["realtime", "delayed"] = "delayed",
        poll_interval: float | None = None,
        client: Any = None,  # injectable for tests
        clock: Callable[[], float] = time.time,
        today: Callable[[], date] = lambda: datetime.now(timezone.utc).date(),
    ) -> None:
        self._api_key = api_key
        self._cache = cache
        self._plan = plan
        self._interval = poll_interval or MASSIVE_POLL_INTERVAL_S[plan]
        self._client = client
        self._clock = clock
        self._today = today
        self._tickers: set[str] = set()  # touched only on the event loop
        self._task: asyncio.Task | None = None
        self._poll_lock = asyncio.Lock()
        self._mode = Mode.SNAPSHOT
        self._eod: EodData | None = None
        self._failures = 0
        self.kind = (
            SourceKind.MASSIVE_REALTIME if plan == "realtime" else SourceKind.MASSIVE_DELAYED
        )
        self.status = SourceStatus.STARTING

    # --- lifecycle ---------------------------------------------------------------
    async def start(self, tickers: list[str]) -> None:
        self._client = self._client or RESTClient(api_key=self._api_key)
        for raw in tickers:
            try:
                self._tickers.add(normalize_ticker(raw))
            except Exception:
                logger.warning("Massive: ignoring invalid ticker %r at start", raw)
        await self._poll_once()  # never raises; decides snapshot vs EOD mode
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive started: mode=%s kind=%s status=%s tickers=%d",
            self._mode.value,
            self.kind.value,
            self.status.value,
            len(self._tickers),
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        t = normalize_ticker(ticker)
        if t in self._tickers:
            return
        q = (await self.get_quotes([t])).get(t)  # raises QuoteUnavailableError on provider failure
        if q is None:
            raise UnknownTickerError(t)
        self._cache.update(t, q.price, source=q.source, as_of=q.as_of, prev_close=q.prev_close)
        self._tickers.add(t)

    async def remove_ticker(self, ticker: str) -> None:
        t = ticker.strip().upper()
        self._tickers.discard(t)
        self._cache.remove(t)

    def get_tickers(self) -> list[str]:
        return sorted(self._tickers)

    # --- one-shot ----------------------------------------------------------------
    async def get_quotes(self, tickers: list[str]) -> dict[str, Quote]:
        wanted = sorted({normalize_ticker(t) for t in tickers})
        if not wanted:
            return {}
        if self._mode is Mode.SNAPSHOT:
            try:
                snaps = await self._fetch(wanted)
            except _NotEntitledError:
                self._enter_eod_mode()
            else:
                now = self._clock()
                out: dict[str, Quote] = {}
                for snap in snaps:
                    p = pick_price(snap, now)
                    if p and snap.ticker in wanted:
                        out[snap.ticker] = Quote(
                            snap.ticker,
                            p.price,
                            p.price,
                            p.prev_close,
                            p.as_of,
                            now,
                            self._source_for(p.tier),
                        )
                return out
        eod = await self._ensure_eod()
        return {t: q for t in wanted if (q := self._eod_quote(t, eod, self._clock()))}

    async def get_eod_closes(self, tickers: list[str], on: date | None = None) -> dict[str, float]:
        wanted = {normalize_ticker(t) for t in tickers}
        try:
            if on is None:
                bars = (await self._ensure_eod()).latest
            else:
                bars = await asyncio.to_thread(grouped_bars, self._client, on)
        except QuoteUnavailableError:
            raise
        except Exception as exc:
            raise QuoteUnavailableError(f"Massive EOD request failed: {exc}") from exc
        return {t: b.close for t, b in bars.items() if t in wanted}

    # --- polling -----------------------------------------------------------------
    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._next_delay())
            await self._poll_once()

    def _next_delay(self) -> float:
        if self._failures:
            base = EOD_RETRY_S if self._mode is Mode.EOD else self._interval
            return min(base * 2 ** (self._failures - 1), MASSIVE_BACKOFF_MAX_S)
        return EOD_REFRESH_S if self._mode is Mode.EOD else self._interval

    async def _poll_once(self) -> None:
        """One cycle. Never raises: failures become status + log, old quotes are kept."""
        async with self._poll_lock:
            if self._mode is Mode.SNAPSHOT:
                if not self._tickers:
                    if self.status is SourceStatus.STARTING:
                        self.status = SourceStatus.OK
                    return
                try:
                    snaps = await self._fetch(sorted(self._tickers))
                except _NotEntitledError:
                    self._enter_eod_mode()  # fall through to the EOD path below
                except QuoteUnavailableError as exc:
                    self._failures += 1
                    if self.status is not SourceStatus.ERROR:
                        self.status = SourceStatus.DEGRADED
                    logger.error("Massive poll failed (%d in a row): %s", self._failures, exc)
                    return
                else:
                    self._apply_snapshots(snaps)
                    self._failures = 0
                    self.status = SourceStatus.OK
                    return
            await self._poll_eod()

    async def _poll_eod(self) -> None:
        try:
            eod = await self._ensure_eod(force=True)
        except QuoteUnavailableError as exc:
            self._failures += 1
            self.status = SourceStatus.DEGRADED
            logger.error("Massive EOD fetch failed: %s", exc)
            return
        now = self._clock()
        for t in self._tickers:
            self._store_eod(t, eod, now)
        self._failures = 0
        self.status = SourceStatus.OK

    # --- internals ---------------------------------------------------------------
    def _source_for(self, tier: PriceTier) -> SourceKind:
        return SourceKind.MASSIVE_EOD if tier == "prev_close" else self.kind

    def _apply_snapshots(self, snaps: Iterable[Any]) -> None:
        now = self._clock()
        seen = 0
        for snap in snaps:
            if snap.ticker not in self._tickers:  # removed while the request was in flight
                continue
            p = pick_price(snap, now)
            if p is None:
                logger.warning("Massive: no usable price for %s", snap.ticker)
                continue
            self._cache.update(
                snap.ticker,
                p.price,
                source=self._source_for(p.tier),
                as_of=p.as_of,
                prev_close=p.prev_close,
            )
            seen += 1
        logger.debug("Massive poll: %d/%d tickers priced", seen, len(self._tickers))

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        """Blocking SDK calls, run in a worker thread. Chunked to keep URLs short."""
        out: list = []
        for i in range(0, len(tickers), MASSIVE_CHUNK_SIZE):
            out.extend(
                self._client.get_snapshot_all(
                    market_type=SnapshotMarketType.STOCKS,
                    tickers=tickers[i : i + MASSIVE_CHUNK_SIZE],
                )
            )
        return out

    async def _fetch(self, tickers: list[str]) -> list:
        """Snapshot fetch with errors translated to what the callers act on."""
        try:
            return await asyncio.to_thread(self._fetch_snapshots, tickers)
        except Exception as exc:
            kind = classify_error(exc)
            if kind == "forbidden":
                raise _NotEntitledError from exc
            if kind == "auth":
                self.status = SourceStatus.ERROR
                raise QuoteUnavailableError("Massive rejected the API key") from exc
            raise QuoteUnavailableError(f"Massive request failed ({kind}): {exc}") from exc

    def _enter_eod_mode(self) -> None:
        if self._mode is not Mode.EOD:
            logger.warning("Massive plan lacks the snapshot endpoint; using end-of-day prices")
        self._mode = Mode.EOD
        self.kind = SourceKind.MASSIVE_EOD

    async def _ensure_eod(self, force: bool = False) -> EodData:
        fresh = self._eod and self._clock() - self._eod.fetched_at < EOD_REFRESH_S
        if self._eod is None or force or not fresh:
            try:
                self._eod = await asyncio.to_thread(
                    fetch_eod_sessions, self._client, self._today(), self._clock
                )
            except Exception as exc:
                if classify_error(exc) == "auth":
                    self.status = SourceStatus.ERROR
                raise QuoteUnavailableError(f"Massive EOD request failed: {exc}") from exc
        return self._eod

    def _eod_quote(self, t: str, eod: EodData, now: float) -> Quote | None:
        bar = eod.latest.get(t)
        if bar is None:
            return None
        prior = eod.prior.get(t)
        return Quote(
            t,
            bar.close,
            bar.close,
            prior.close if prior else None,
            bar.as_of,
            now,
            SourceKind.MASSIVE_EOD,
        )

    def _store_eod(self, t: str, eod: EodData, now: float) -> None:
        q = self._eod_quote(t, eod, now)
        if q:
            self._cache.update(t, q.price, source=q.source, as_of=q.as_of, prev_close=q.prev_close)
```

How the pieces fit:

- `pick_price` and `classify_error` are pure and carry most of the logic, so they are tested without a network.
- `_fetch` runs the blocking SDK call in a thread and translates failures into what callers act on: `_NotEntitledError` (switch mode), `QuoteUnavailableError` (degrade), and a status change on auth failure.
- `_poll_once` holds a lock so a mode switch cannot interleave with another poll, and **never raises**.
- `get_quotes` handles a 403 itself, so a trade in an untracked ticker works even before the first poll ever ran (empty watchlist at start).
- The EOD walk-back skips weekends locally and pays one empty call per holiday. A refresh typically costs two to four calls (more only around multi-day holidays; the walk-back is capped at 7 days). On the free plan (5 calls/min) a rate-limit error during it degrades and retries after `EOD_RETRY_S`.

## 9. Factory

**`backend/app/market/factory.py`**

```python
"""Chooses the market data source from the environment."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def create_market_data_source(cache: PriceCache) -> MarketDataSource:
    """MASSIVE_API_KEY non-empty -> Massive; otherwise the simulator. Returns an unstarted source.

    Environment:
      MASSIVE_API_KEY   unset / empty / whitespace -> simulator
      MASSIVE_PLAN      "realtime" | "delayed" (default "delayed", the safe label)
      SIM_SEED          integer; makes the simulator deterministic (E2E / debugging)
    """
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if key:
        plan = os.environ.get("MASSIVE_PLAN", "delayed").strip().lower()
        if plan not in ("realtime", "delayed"):
            logger.warning("Unknown MASSIVE_PLAN %r; using 'delayed'", plan)
            plan = "delayed"
        logger.info("Market data source: Massive (%s)", plan)
        return MassiveDataSource(api_key=key, cache=cache, plan=plan)  # type: ignore[arg-type]

    seed = os.environ.get("SIM_SEED", "").strip()
    logger.info("Market data source: GBM simulator")
    return SimulatorDataSource(cache=cache, seed=int(seed) if seed else None)
```

Whitespace-only `MASSIVE_API_KEY` counts as unset. The decision is logged once; `GET /api/health` reports it (section 12).

## 10. MarketService

The sources deliberately do not know the watchlist, the positions or the freshness policy. `MarketService` is the single object the rest of the backend holds. It owns three things:

1. **The tracked set** = watchlist ∪ open positions. `reconcile()` is idempotent and serialised with a lock; call it after every watchlist change, every trade, every chat action batch and a reset. It returns tickers it could not add instead of raising, so one bad symbol cannot block the rest.
2. **Which price is eligible.** `quote_for_trade()` = fresh cached quote, else a one-shot `get_quotes()`, and the one-shot result is itself freshness-checked. `valuation_quote()` returns `None` rather than a stale or missing price.
3. **Health.** Availability and staleness are reported separately from the SSE connection dot.

**`backend/app/market/service.py`**

```python
"""MarketService: owns the tracked set and the rules for using prices."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable

from .cache import PriceCache
from .config import FRESH_LIMIT_S
from .errors import MarketDataError, QuoteUnavailableError, UnknownTickerError
from .interface import MarketDataSource
from .models import Quote, SourceKind
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)


class MarketService:
    """What the rest of the backend talks to (routes, execute_trade, chat, snapshot task).

    Responsibilities the sources deliberately do not have:
      * tracked set = watchlist UNION open positions (reconcile)
      * which price is eligible for a trade or a valuation (freshness)
      * health reporting
    """

    def __init__(
        self,
        cache: PriceCache,
        source: MarketDataSource,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cache = cache
        self.source = source
        self._clock = clock
        self._lock = asyncio.Lock()

    # --- lifecycle ---------------------------------------------------------------
    async def start(self, watchlist: Iterable[str], held: Iterable[str]) -> None:
        desired = _normalise(watchlist) | _normalise(held)
        await self.source.start(sorted(desired))

    async def stop(self) -> None:
        await self.source.stop()

    async def reconcile(self, watchlist: Iterable[str], held: Iterable[str]) -> list[str]:
        """Make the source track exactly watchlist | held. Idempotent; returns tickers that
        could not be added. Call after ANY change to the watchlist or to positions
        (watchlist add/remove, trade, chat action, reset)."""
        desired = _normalise(watchlist) | _normalise(held)
        failed: list[str] = []
        async with self._lock:
            current = set(self.source.get_tickers())
            for t in sorted(desired - current):
                try:
                    await self.source.add_ticker(t)
                except MarketDataError as exc:
                    logger.warning("Cannot track %s: %s", t, exc)
                    failed.append(t)
            for t in sorted(current - desired):
                await self.source.remove_ticker(t)
        return failed

    async def reset(self, watchlist: Iterable[str]) -> None:
        """POST /api/reset: positions are gone, so the tracked set is just the watchlist."""
        await self.reconcile(watchlist, held=())
        await self.source.reset()

    # --- using prices ------------------------------------------------------------
    async def quote_for_trade(self, ticker: str) -> Quote:
        """The one quote a trade fills at. Raises InvalidTickerError / UnknownTickerError / QuoteUnavailableError.

        Fresh cache hit first; otherwise a one-shot fetch (a supported ticker that is not
        tracked yet, or a stale feed). The caller must reconcile() after the trade commits.
        """
        t = normalize_ticker(ticker)
        q = self.cache.get_fresh(t)
        if q is not None:
            return q
        quotes = await self.source.get_quotes([t])  # may raise QuoteUnavailableError
        q = quotes.get(t)
        if q is None:
            if self.cache.get(t) is not None:
                raise QuoteUnavailableError(f"price for {t} is stale")
            raise UnknownTickerError(t)
        if q.age(self._clock()) > FRESH_LIMIT_S[q.source]:  # e.g. a dead simulator loop
            raise QuoteUnavailableError(f"price for {t} is stale")
        return q

    def valuation_quote(self, ticker: str) -> Quote | None:
        """Quote for portfolio valuation, or None. Callers must treat None as 'unknown',
        never as zero: skip the snapshot, or mark the position unpriced."""
        return self.cache.get_fresh(normalize_ticker(ticker))

    # --- reporting ---------------------------------------------------------------
    def health(self) -> dict:
        tracked = self.source.get_tickers()
        stale = [t for t in tracked if self.cache.get_fresh(t) is None]
        return {
            "market_source": self.source.kind.value,
            "market_status": self.source.status.value,
            "end_of_day": self.source.kind is SourceKind.MASSIVE_EOD,
            "delayed": self.source.kind is SourceKind.MASSIVE_DELAYED,
            "tracked": len(tracked),
            "stale": stale,
        }


def _normalise(tickers: Iterable[str]) -> set[str]:
    return {normalize_ticker(t) for t in tickers}
```

`__init__.py`:

**`backend/app/market/__init__.py`**

```python
"""Market data subsystem. Nothing outside this package imports `massive` or the simulator."""

from .cache import PriceCache
from .errors import InvalidTickerError, MarketDataError, QuoteUnavailableError, UnknownTickerError
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import Quote, SourceKind, SourceStatus
from .service import MarketService
from .stream import create_stream_router
from .tickers import normalize_ticker

__all__ = [
    "InvalidTickerError",
    "MarketDataError",
    "MarketDataSource",
    "MarketService",
    "PriceCache",
    "Quote",
    "QuoteUnavailableError",
    "SourceKind",
    "SourceStatus",
    "UnknownTickerError",
    "create_market_data_source",
    "create_stream_router",
    "normalize_ticker",
]
```

## 11. SSE stream

`GET /api/stream/prices` — `text/event-stream`. Sent whenever `cache.version` changes, checked every 0.5 s. The stream is a **full snapshot** keyed by ticker, not a delta: a client replaces its map, and a ticker absent from an event has been removed.

Wire format (times are unix seconds; one event is one line, wrapped here for reading):

```
retry: 1000

data: {"AAPL": {"ticker": "AAPL", "price": 190.52, "prev_price": 190.49, "prev_close": 189.1,
                "day_change": 1.42, "day_change_pct": 0.751, "direction": "up",
                "as_of": 1760649600.5, "source": "simulator"},
       "TSLA": {...}}

: keep-alive

data: {}
```

| Situation | Behaviour |
|---|---|
| (Re)connect | Event sent immediately, even if the cache is empty (`{}`). |
| Nothing changed | No event; a `: keep-alive` comment every 15 s keeps proxies from closing the connection. |
| Last ticker removed | `{}` is sent so the client can clear. |
| Client disconnect | The generator ends on `is_disconnected()`; cancellation is re-raised. |
| `source` | `simulator`, `massive-realtime`, `massive-delayed`, `massive-eod`; the UI shows a "delayed" or "end-of-day" banner from it (or from `/api/health`). |

The generator takes an `is_disconnected` callable and a clock so tests drive the real code (section 14).

**`backend/app/market/stream.py`**

```python
"""SSE endpoint: GET /api/stream/prices."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)


def create_stream_router(cache: PriceCache) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])  # new router per call

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            generate_events(cache, request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


async def generate_events(
    cache: PriceCache,
    is_disconnected: Callable[[], Awaitable[bool]],
    interval: float = 0.5,
    heartbeat: float = 15.0,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncGenerator[str, None]:
    """Full-snapshot semantics.

    * Every event is the whole cache keyed by ticker (not a delta), so a client can simply
      replace its map. Tickers missing from an event have been removed.
    * An event is sent immediately on (re)connect, even if the cache is empty (`{}`).
    * After that, an event is sent only when cache.version changes.
    * A `: keep-alive` comment goes out when idle so proxies keep the connection open.
    """
    yield "retry: 1000\n\n"
    last_version = -1
    last_sent = clock()
    try:
        while not await is_disconnected():
            version, quotes = cache.snapshot()
            if version != last_version:
                last_version = version
                payload = json.dumps({t: q.to_dict() for t, q in quotes.items()})
                yield f"data: {payload}\n\n"
                last_sent = clock()
            elif clock() - last_sent >= heartbeat:
                yield ": keep-alive\n\n"
                last_sent = clock()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.debug("SSE stream cancelled")
        raise
```

Frontend consumption (sketch, TypeScript): skip repeated points by `as_of`, bound history.

```ts
const MAX_POINTS = 600;                       // ~5 min at 2 ticks/s; per ticker
type Quote = { ticker: string; price: number; prev_price: number; prev_close: number | null;
               day_change: number | null; day_change_pct: number | null;
               direction: "up" | "down" | "flat"; as_of: number; source: string };

const es = new EventSource("/api/stream/prices");
es.onmessage = (e) => {
  const quotes: Record<string, Quote> = JSON.parse(e.data);
  setQuotes(quotes);                                           // replace, do not merge
  setHistory((h) => {
    const next = { ...h };
    for (const [t, q] of Object.entries(quotes)) {
      const series = next[t] ?? [];
      if (series.at(-1)?.time === q.as_of) continue;           // Massive repeats the same trade
      next[t] = [...series, { time: q.as_of, value: q.price }].slice(-MAX_POINTS);
    }
    for (const t of Object.keys(next)) if (!(t in quotes)) delete next[t];   // removed tickers
    return next;
  });
};
es.onerror = () => setConnection("reconnecting");              // EventSource retries by itself
es.onopen = () => setConnection("connected");
```

## 12. App wiring and usage

### 12.1 Lifespan, health, error mapping

The cache, source and service are built when the app is created; the stream router is included at that time (a static route table), not inside `lifespan`. Startup order, per REVIEW 8: initialise and seed the DB, load watchlist and positions, **then** start the source. This file ran under uvicorn with the market data only; the two loader callables stand in for the DB layer that does not exist yet.

**`backend/app/main.py`**

```python
"""Wiring sketch: market data parts of the FastAPI app. DB access is stubbed by two callables."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from app.market import (
    InvalidTickerError,
    MarketService,
    PriceCache,
    QuoteUnavailableError,
    UnknownTickerError,
    create_market_data_source,
    create_stream_router,
)


def create_app(
    load_watchlist: Callable[[], Awaitable[list[str]]],
    load_held: Callable[[], Awaitable[list[str]]],
) -> FastAPI:
    cache = PriceCache()
    service = MarketService(cache, create_market_data_source(cache))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # DB is initialised/seeded BEFORE this point; tracked set = watchlist | open positions.
        await service.start(await load_watchlist(), await load_held())
        yield
        await service.stop()

    app = FastAPI(title="FinAlly", lifespan=lifespan)
    app.state.market = service
    app.include_router(create_stream_router(cache))  # static route table, not built in lifespan

    @app.get("/api/health")
    async def health(request: Request):
        return {"status": "ok", **request.app.state.market.health()}

    return app


def get_market(request: Request) -> MarketService:
    return request.app.state.market


def http_error(exc: Exception) -> HTTPException:
    """Single mapping from market errors to HTTP, shared by trade/watchlist/chat paths."""
    if isinstance(exc, (InvalidTickerError, UnknownTickerError)):
        return HTTPException(400, str(exc))
    if isinstance(exc, QuoteUnavailableError):
        return HTTPException(503, f"Price unavailable: {exc}")
    raise exc
```

`/api/health` response (simulator):

```json
{"status": "ok", "market_source": "simulator", "market_status": "ok",
 "end_of_day": false, "delayed": false, "tracked": 3, "stale": []}
```

### 12.2 Using it from other components (sketches; DB calls are placeholders)

**Manual and chat trades** (one shared `execute_trade`). Take the quote *before* opening the DB write transaction (network calls stay out of the transaction), fill at `quote.price`, reconcile after commit:

```python
async def execute_trade(market: MarketService, db, ticker: str, side: str, qty: float):
    quote = await market.quote_for_trade(ticker)          # InvalidTickerError / UnknownTickerError / QuoteUnavailableError
    with db.write_transaction():                          # BEGIN IMMEDIATE (REVIEW 1)
        ...validate cash / shares against quote.price, write account, position, trade, snapshot...
    await market.reconcile(db.watchlist_tickers(), db.held_tickers())
    return {"price": quote.price, "source": quote.source.value}
```

Routes convert errors with `http_error(exc)`. Chat actions record the same exception text as the per-action result.

**Watchlist add** — validate by asking the source for a quote *first*, so a bad symbol never reaches the DB:

```python
@router.post("/api/watchlist")
async def add_watch(body: WatchlistAdd, market: MarketService = Depends(get_market)):
    try:
        ticker = normalize_ticker(body.ticker)
        await market.quote_for_trade(ticker)              # proves it is priceable
    except (InvalidTickerError, UnknownTickerError, QuoteUnavailableError) as exc:
        raise http_error(exc)
    db.insert_watchlist(ticker)
    await market.reconcile(db.watchlist_tickers(), db.held_tickers())
    return {"ticker": ticker}

@router.delete("/api/watchlist/{ticker}")
async def remove_watch(ticker: str, market: MarketService = Depends(get_market)):
    db.delete_watchlist(normalize_ticker(ticker))
    await market.reconcile(db.watchlist_tickers(), db.held_tickers())   # held tickers stay tracked
    return {"status": "ok"}
```

**Portfolio valuation and the 30-second snapshot task.** An unpriced position must never count as $0 or be persisted as such:

```python
def portfolio_value(cash: float, positions, market: MarketService):
    total, unpriced = cash, []
    for p in positions:
        q = market.valuation_quote(p.ticker)
        if q is None:
            unpriced.append(p.ticker)
            continue
        total += p.quantity * q.price
    return total, unpriced          # snapshot task: if unpriced, skip this tick and log

async def snapshot_loop(market, db):
    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL_S)
        value, unpriced = portfolio_value(db.cash(), db.positions(), market)
        if not unpriced:
            db.record_snapshot(value)
```

**Reset** (`POST /api/reset`), after the DB rows are cleared and the default watchlist restored: `await market.reset(db.watchlist_tickers())` → positions are gone so the tracked set is the watchlist, and the simulator restarts from seeds with a fresh `prev_close`.

**Chat context** — live prices for the prompt come from `cache.get_all()` (or `valuation_quote` per ticker); include `source` so the model does not present an EOD price as live.

## 13. Failure matrix

| Failure | Detected by | Result |
|---|---|---|
| Bad symbol format | `normalize_ticker` | `InvalidTickerError` → 400 |
| Symbol not in simulator catalog / absent from Massive | `add_ticker`, `get_quotes` | `UnknownTickerError` → 400 |
| Provider timeout / 5xx / 429 | `_fetch` → `classify_error` | poll: `degraded` + backoff, old quotes kept but stale; one-shot: `QuoteUnavailableError` → 503 |
| Plan lacks snapshot (403 body) | `classify_error == "forbidden"` | EOD mode, banner via `/api/health` |
| Bad API key (401 body / `AuthError`) | `classify_error == "auth"` | `status = error`; one-shot: `QuoteUnavailableError` |
| Price `None`/NaN/≤0 from provider | `pick_price`, `PriceCache.update` | skipped; old quote kept |
| Simulator step raises | `_run_loop` | logged; loop continues |
| Simulator loop dead | freshness | quotes stale after 5 s; trades get `QuoteUnavailableError`; `/api/health.stale` lists them |
| Massive poll thread raises something unexpected | `_fetch` `except Exception` | `QuoteUnavailableError`/`degraded`, never an unhandled task error |
| Empty watchlist and no positions at start | `_poll_once` skips the call | status `ok`, `{}` on the stream, plan detected on first `add_ticker` |
| Held ticker rejected by the source at boot (stale DB row) | `reconcile` returns it in `failed` | logged; valuation treats it as unpriced |

## 14. Tests

Run from `backend/` with the existing `pyproject.toml` config (`asyncio_mode = "auto"`): `uv run pytest tests/market`. Massive fixtures are built with the SDK's own `TickerSnapshot.from_dict` / `GroupedDailyAgg.from_dict`, so attribute names and units are checked by the SDK rather than by a `MagicMock`. The SSE tests drive the real generator; Starlette's `TestClient` buffers responses and cannot read an infinite stream, so do not use it for this.

Coverage map:

| Area | File | Cases |
|---|---|---|
| Cache, Quote, time | `test_cache_and_quote.py` | invalid prices rejected; `prev_close` survives updates; direction vs day-change baselines differ; version moves only on visible change; remove bumps version; freshness by fetch age and by source; unit conversion |
| Simulator | `test_simulator.py` | determinism by seed; log-return σ within 5 %; shock size; Cholesky for 1/2/10/27 tickers; unknown/invalid tickers; baseline at start; loop survives an exception; reset; one-shot methods |
| Massive | `test_massive.py` | every price tier from SDK models; ns vs ms; error classification table; snapshot mode; 403 → EOD once; EOD weekend/holiday walk-back; 401 → `error`; failed poll → `degraded`, retained but stale, then recovers; add with first quote / unknown / outage; `get_quotes` omits and does not cache; no resurrection after removal |
| Stream, service | `test_stream_service.py` | initial snapshot then changes only; `{}` on empty and after removal; heartbeat; reconcile union and idempotence; untrackable reported; `quote_for_trade` paths; stale valuation; health |
| Contract (both sources) | `test_contract.py` | start/stop idempotent; `add_ticker` yields a cache entry or raises; `get_quotes` omits unpriceable, never zero |

### 14.1 Shared fixtures

**`backend/tests/conftest.py`**

```python
import pytest

from app.market.cache import PriceCache


class Clock:
    """Controllable wall clock."""

    def __init__(self, t: float = 1_760_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cache(clock) -> PriceCache:
    return PriceCache(clock=clock)
```

### 14.2 Cache, quote, time

**`backend/tests/market/test_cache_and_quote.py`**

```python
import math

import pytest

from app.market.models import Quote, SourceKind
from app.market.timeutil import to_unix_seconds

SIM = SourceKind.SIMULATOR


@pytest.mark.parametrize("bad", [0, -1, math.nan, math.inf, None, True])
def test_rejects_invalid_prices_and_keeps_old_quote(cache, bad):
    cache.update("AAPL", 190.0, source=SIM)
    v = cache.version
    assert cache.update("AAPL", bad, source=SIM) is None
    assert cache.get("AAPL").price == 190.0 and cache.version == v


def test_prev_close_survives_updates(cache):
    cache.update("AAPL", 190.0, source=SIM, prev_close=188.0)
    q = cache.update("AAPL", 191.0, source=SIM)
    assert q.prev_close == 188.0


def test_direction_and_day_change_use_different_baselines(cache):
    cache.update("AAPL", 190.0, source=SIM, prev_close=200.0)
    q = cache.update("AAPL", 190.5, source=SIM)
    assert q.direction == "up"  # vs previous tick
    assert q.day_change_pct < 0  # vs prev_close
    assert q.day_change == -9.5


def test_version_moves_only_on_visible_change(cache, clock):
    cache.update("AAPL", 190.0, source=SIM, as_of=1.0)
    v = cache.version
    clock.advance(10)
    cache.update("AAPL", 190.0, source=SIM, as_of=1.0)  # same visible content
    assert cache.version == v
    assert cache.get("AAPL").fetched_at == clock()  # but freshness was renewed


def test_remove_and_clear_bump_version(cache):
    cache.update("AAPL", 190.0, source=SIM)
    v = cache.version
    cache.remove("AAPL")
    assert cache.version == v + 1 and cache.get("AAPL") is None
    cache.remove("AAPL")  # no-op: no bump
    assert cache.version == v + 1


def test_get_fresh_uses_fetch_age_and_source_limit(cache, clock):
    cache.update("AAPL", 190.0, source=SIM)
    assert cache.get_fresh("AAPL") is not None
    clock.advance(6)  # simulator limit is 5 s
    assert cache.get_fresh("AAPL") is None
    assert cache.get_fresh("AAPL", max_age=10) is not None
    cache.update("OLD", 50.0, source=SourceKind.MASSIVE_EOD, as_of=1.0)
    clock.advance(10**6)
    assert cache.get_fresh("OLD") is not None  # EOD never expires


def test_quote_to_dict_is_json_ready():
    q = Quote("AAPL", 101.0, 100.0, 99.0, 1.0, 2.0, SIM)
    d = q.to_dict()
    assert d["direction"] == "up" and d["source"] == "simulator"
    assert d["day_change_pct"] == pytest.approx(2.0202, abs=1e-3) and "fetched_at" not in d


def test_to_unix_seconds_units():
    assert to_unix_seconds(1_760_000_000_000_000_000, "ns") == 1_760_000_000.0
    assert to_unix_seconds(1_760_000_000_000, "ms") == 1_760_000_000.0
    assert to_unix_seconds(None, "ns") is None and to_unix_seconds(0, "ms") is None
    assert to_unix_seconds(math.nan, "s") is None
```

### 14.3 Simulator

**`backend/tests/market/test_simulator.py`**

```python
import asyncio
import math
import statistics

import pytest

from app.market.errors import InvalidTickerError, UnknownTickerError
from app.market.models import SourceKind
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import GBMSimulator, SimulatorDataSource, dt_for_interval


def test_same_seed_same_path():
    a, b = GBMSimulator(["AAPL", "MSFT"], seed=7), GBMSimulator(["AAPL", "MSFT"], seed=7)
    assert [a.step() for _ in range(100)] == [b.step() for _ in range(100)]


def test_log_return_std_matches_sigma():
    sim = GBMSimulator(["AAPL"], event_probability=0, seed=1)
    prev, rets = sim.get_price("AAPL"), []  # use full-precision state, not rounded step() output
    for _ in range(20_000):
        sim.step()
        cur = sim._prices["AAPL"]
        rets.append(math.log(cur / prev))
        prev = cur
    expected = 0.22 * math.sqrt(dt_for_interval(0.5))
    assert statistics.stdev(rets) == pytest.approx(expected, rel=0.05)


def test_shock_is_2_to_5_percent():
    sim = GBMSimulator(["AAPL"], event_probability=1.0, seed=3)
    before = sim.get_price("AAPL")
    sim.step()
    ratio = sim._prices["AAPL"] / before
    assert 0.02 <= abs(ratio - 1) <= 0.0505


@pytest.mark.parametrize("n", [1, 2, 10, 27])
def test_cholesky_succeeds_for_any_catalog_subset(n):
    sim = GBMSimulator(list(SEED_PRICES)[:n], seed=0)
    assert all(p > 0 for p in sim.step().values())


def test_unknown_ticker_rejected():
    with pytest.raises(UnknownTickerError):
        GBMSimulator(["ZZZZ"])


async def test_start_seeds_cache_with_baseline(cache, clock):
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1, clock=clock)
    await src.start(["AAPL"])
    q = cache.get("AAPL")
    assert q.price == SEED_PRICES["AAPL"] and q.prev_close == SEED_PRICES["AAPL"]
    await asyncio.sleep(0.1)
    q2 = cache.get("AAPL")
    assert q2.prev_close == SEED_PRICES["AAPL"] and q2.source is SourceKind.SIMULATOR
    await src.stop()
    await src.stop()  # idempotent


async def test_add_ticker_policy(cache):
    src = SimulatorDataSource(cache, update_interval=10, seed=1)
    await src.start(["AAPL"])
    await src.add_ticker(" pypl ")
    assert cache.get("PYPL") is not None and "PYPL" in src.get_tickers()
    with pytest.raises(UnknownTickerError):
        await src.add_ticker("ZZZZ")
    with pytest.raises(InvalidTickerError):
        await src.add_ticker("123")
    await src.remove_ticker("PYPL")
    assert cache.get("PYPL") is None
    await src.stop()


async def test_loop_survives_step_exception(cache):
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL"])
    calls = {"n": 0}
    real = src._sim.step

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real()

    src._sim.step = flaky
    await asyncio.sleep(0.1)
    assert calls["n"] > 2
    await src.stop()


async def test_reset_restores_seed_and_baseline(cache):
    src = SimulatorDataSource(cache, update_interval=10, event_probability=1.0, seed=1)
    await src.start(["AAPL"])
    for _ in range(5):
        src._sim.step()
    await src.reset()
    assert cache.get("AAPL").price == SEED_PRICES["AAPL"]
    await src.stop()


async def test_one_shot_methods(cache):
    src = SimulatorDataSource(cache, update_interval=10, seed=1)
    await src.start(["AAPL"])
    qs = await src.get_quotes(["AAPL", "PYPL", "ZZZZ"])  # PYPL known but untracked
    assert set(qs) == {"AAPL", "PYPL"} and "PYPL" not in src.get_tickers()
    assert await src.get_eod_closes(["AAPL", "ZZZZ"]) == {"AAPL": SEED_PRICES["AAPL"]}
    await src.stop()
```

### 14.4 Massive

**`backend/tests/market/test_massive.py`**

```python
import json
from datetime import date

import pytest
from massive.exceptions import AuthError, BadResponse
from massive.rest.models import GroupedDailyAgg, TickerSnapshot

from app.market.errors import QuoteUnavailableError, UnknownTickerError
from app.market.massive_client import MassiveDataSource, classify_error, pick_price
from app.market.models import SourceKind, SourceStatus

NS = 1_760_649_600_000_000_000  # 2025-10-16 20:00:00 UTC in ns
MS = 1_760_649_540_000


def snap(ticker="AAPL", **kw) -> TickerSnapshot:
    return TickerSnapshot.from_dict({"ticker": ticker, **kw})


class FakeClient:
    def __init__(self, snaps=(), grouped=None, error=None):
        self.snaps, self.grouped, self.error, self.calls = list(snaps), grouped or {}, error, []

    def get_snapshot_all(self, market_type, tickers):
        self.calls.append(("snap", tuple(tickers)))
        if self.error:
            raise self.error
        return [s for s in self.snaps if s.ticker in tickers]

    def get_grouped_daily_aggs(self, on, adjusted=True):
        self.calls.append(("grouped", on))
        return self.grouped.get(on, [])


def bars(d, **closes):
    return {
        d: [
            GroupedDailyAgg.from_dict({"T": t, "c": c, "t": 1_760_000_000_000})
            for t, c in closes.items()
        ]
    }


# --- pick_price: every fallback tier, with SDK-deserialised models -------------------
def test_price_prefers_last_trade_and_converts_ns():
    p = pick_price(
        snap(lastTrade={"p": 190.5, "t": NS}, min={"c": 1.0, "t": MS}, prevDay={"c": 189.0}), now=0
    )
    assert (p.price, p.tier, p.prev_close) == (190.5, "trade", 189.0)
    assert p.as_of == NS / 1e9


def test_price_falls_back_to_minute_then_day_then_prev_close():
    assert pick_price(snap(min={"c": 190.4, "t": MS}), 0).as_of == MS / 1e3
    d = pick_price(snap(day={"c": 190.0}, updated=NS), 0)
    assert (d.tier, d.as_of) == ("day", NS / 1e9)
    pc = pick_price(snap(day={"c": 0}, prevDay={"c": 189.0, "t": MS}), 0)
    assert (pc.tier, pc.price, pc.prev_close) == ("prev_close", 189.0, None)


def test_price_none_when_nothing_usable():
    assert pick_price(snap(lastTrade={"p": 0}, day={"c": 0}), 0) is None
    assert pick_price(snap(), 0) is None


# --- error classification ----------------------------------------------------------
@pytest.mark.parametrize(
    "exc,expected",
    [
        (AuthError("Missing API key"), "auth"),
        (
            BadResponse(
                json.dumps({"status": "NOT_AUTHORIZED", "message": "You are not entitled..."})
            ),
            "forbidden",
        ),
        (BadResponse(json.dumps({"status": "ERROR", "message": "Unknown API Key"})), "auth"),
        (
            BadResponse(
                json.dumps(
                    {"status": "ERROR", "error": "You've exceeded the maximum requests per minute"}
                )
            ),
            "rate_limited",
        ),
        (BadResponse("<html>502</html>"), "other"),
        (TimeoutError("t"), "other"),
    ],
)
def test_classify_error(exc, expected):
    assert classify_error(exc) == expected


# --- polling behaviour -------------------------------------------------------------
async def make(cache, clock, client, **kw):
    src = MassiveDataSource(
        "k",
        cache,
        client=client,
        clock=clock,
        poll_interval=3600,
        today=lambda: date(2025, 10, 17),
        **kw,
    )
    await src.start(["AAPL"])
    return src


async def test_snapshot_mode_populates_cache(cache, clock):
    c = FakeClient([snap(lastTrade={"p": 190.5, "t": NS}, prevDay={"c": 189.0})])
    src = await make(cache, clock, c, plan="realtime")
    q = cache.get("AAPL")
    assert (q.price, q.prev_close, q.source) == (190.5, 189.0, SourceKind.MASSIVE_REALTIME)
    assert src.status is SourceStatus.OK
    await src.stop()


async def test_403_switches_to_eod_mode_once(cache, clock):
    err = BadResponse(json.dumps({"status": "NOT_AUTHORIZED", "message": "not entitled"}))
    c = FakeClient(
        error=err,
        grouped={**bars(date(2025, 10, 16), AAPL=190.0), **bars(date(2025, 10, 15), AAPL=188.0)},
    )
    src = await make(cache, clock, c)
    q = cache.get("AAPL")
    assert (q.price, q.prev_close, q.source) == (190.0, 188.0, SourceKind.MASSIVE_EOD)
    assert src.kind is SourceKind.MASSIVE_EOD and src.status is SourceStatus.OK
    snap_calls = [x for x in c.calls if x[0] == "snap"]
    await src._poll_once()  # must not retry the snapshot
    assert [x for x in c.calls if x[0] == "snap"] == snap_calls
    await src.stop()


async def test_eod_walks_back_over_weekend_and_holiday(cache, clock):
    # today Fri 2025-10-17: Thu 16 is a "holiday" (empty), Wed 15 and Tue 14 have bars
    c = FakeClient(
        error=BadResponse(json.dumps({"status": "NOT_AUTHORIZED"})),
        grouped={**bars(date(2025, 10, 15), AAPL=190.0), **bars(date(2025, 10, 14), AAPL=187.0)},
    )
    await make(cache, clock, c)
    assert cache.get("AAPL").prev_close == 187.0


async def test_401_sets_error_and_serves_nothing(cache, clock):
    c = FakeClient(error=BadResponse(json.dumps({"status": "ERROR", "message": "Unknown API Key"})))
    src = await make(cache, clock, c)
    assert src.status is SourceStatus.ERROR and cache.get("AAPL") is None
    await src.stop()


async def test_failed_poll_after_success_keeps_old_quote_but_ages_out(cache, clock):
    c = FakeClient([snap(lastTrade={"p": 190.5, "t": NS})])
    src = await make(cache, clock, c)
    c.error = TimeoutError("network")
    clock.advance(100)
    await src._poll_once()
    assert src.status is SourceStatus.DEGRADED
    assert cache.get("AAPL").price == 190.5  # retained
    assert cache.get_fresh("AAPL") is None  # but not trade-eligible
    c.error = None
    await src._poll_once()
    assert src.status is SourceStatus.OK and cache.get_fresh("AAPL") is not None
    await src.stop()


async def test_add_ticker_gets_first_quote_or_raises(cache, clock):
    c = FakeClient(
        [snap(lastTrade={"p": 190.5, "t": NS}), snap("PYPL", lastTrade={"p": 65.0, "t": NS})]
    )
    src = await make(cache, clock, c)
    await src.add_ticker("pypl")
    assert cache.get("PYPL").price == 65.0 and "PYPL" in src.get_tickers()
    with pytest.raises(UnknownTickerError):
        await src.add_ticker("NOPE")
    c.error = TimeoutError("x")
    with pytest.raises(QuoteUnavailableError):
        await src.add_ticker("MSFT")
    assert "MSFT" not in src.get_tickers()
    await src.stop()


async def test_get_quotes_omits_missing_and_does_not_cache(cache, clock):
    c = FakeClient([snap("PYPL", lastTrade={"p": 65.0, "t": NS})])
    src = await make(cache, clock, c)
    qs = await src.get_quotes(["PYPL", "NOPE"])
    assert set(qs) == {"PYPL"} and cache.get("PYPL") is None
    await src.stop()


async def test_removed_while_in_flight_is_not_resurrected(cache, clock):
    c = FakeClient([snap(lastTrade={"p": 190.5, "t": NS})])
    src = await make(cache, clock, c)
    await src.remove_ticker("AAPL")
    src._apply_snapshots(c.snaps)  # a response that raced the removal
    assert cache.get("AAPL") is None
    await src.stop()
```

### 14.5 Stream and service

**`backend/tests/market/test_stream_service.py`**

```python
import json

import pytest

from app.market.errors import InvalidTickerError, QuoteUnavailableError, UnknownTickerError
from app.market.models import SourceKind
from app.market.service import MarketService
from app.market.simulator import SimulatorDataSource
from app.market.stream import generate_events

SIM = SourceKind.SIMULATOR


def parse(chunk: str):
    return json.loads(chunk.removeprefix("data: ").strip())


async def run_stream(cache, ticks, mutate=None):
    t = {"now": 0.0}

    def tick_clock():  # each read advances 1 s so heartbeats can fire
        t["now"] += 1.0
        return t["now"]

    """Drive the REAL generator for `ticks` iterations; `mutate(i)` edits the cache between."""
    state = {"i": 0}

    async def disconnected():
        state["i"] += 1
        if mutate:
            mutate(state["i"])
        return state["i"] > ticks

    return [
        c
        async for c in generate_events(
            cache, disconnected, interval=0, heartbeat=3, clock=tick_clock
        )
    ]


async def test_initial_snapshot_then_only_changes(cache):
    cache.update("AAPL", 190.0, source=SIM, prev_close=188.0)
    out = await run_stream(
        cache, 3, mutate=lambda i: cache.update("AAPL", 191.0, source=SIM) if i == 2 else None
    )
    assert out[0] == "retry: 1000\n\n"
    data = [c for c in out if c.startswith("data:")]
    assert len(data) == 2
    assert (
        parse(data[0])["AAPL"]["price"] == 190.0 and parse(data[0])["AAPL"]["prev_close"] == 188.0
    )
    assert parse(data[1])["AAPL"]["direction"] == "up"


async def test_empty_cache_sends_empty_object_and_removal_clears(cache):
    out = await run_stream(cache, 1)
    assert parse([c for c in out if c.startswith("data:")][0]) == {}
    cache.update("AAPL", 190.0, source=SIM)
    out = await run_stream(cache, 2, mutate=lambda i: cache.remove("AAPL") if i == 2 else None)
    assert [parse(c) for c in out if c.startswith("data:")] == [{"AAPL": parse(out[1])["AAPL"]}, {}]


async def test_heartbeat_when_idle(cache):
    cache.update("AAPL", 190.0, source=SIM)
    out = await run_stream(cache, 5)
    assert ": keep-alive\n\n" in out


# --- MarketService -----------------------------------------------------------------
@pytest.fixture
async def svc(cache, clock):
    s = MarketService(cache, SimulatorDataSource(cache, update_interval=10, seed=1), clock=clock)
    await s.start(["AAPL"], held=["TSLA"])
    yield s
    await s.stop()


async def test_reconcile_is_union_and_idempotent(svc):
    assert sorted(svc.source.get_tickers()) == ["AAPL", "TSLA"]
    assert await svc.reconcile(["AAPL", "MSFT"], held=["TSLA"]) == []
    assert sorted(svc.source.get_tickers()) == ["AAPL", "MSFT", "TSLA"]
    await svc.reconcile(["AAPL"], held=[])  # TSLA sold, MSFT unwatched
    assert svc.source.get_tickers() == ["AAPL"] and svc.cache.get("TSLA") is None
    assert await svc.reconcile(["AAPL"], held=[]) == []


async def test_reconcile_reports_untrackable(svc):
    assert await svc.reconcile(["AAPL", "ZZZZ"], held=[]) == ["ZZZZ"]


async def test_quote_for_trade_paths(svc, clock):
    assert (await svc.quote_for_trade(" aapl ")).ticker == "AAPL"  # fresh cache hit
    assert (await svc.quote_for_trade("PYPL")).ticker == "PYPL"  # untracked, one-shot
    with pytest.raises(UnknownTickerError):
        await svc.quote_for_trade("ZZZZ")
    with pytest.raises(InvalidTickerError):
        await svc.quote_for_trade("A B")
    clock.advance(60)  # feed died: quotes go stale
    with pytest.raises(QuoteUnavailableError):
        await svc.quote_for_trade("AAPL")


async def test_valuation_quote_is_none_when_stale(svc, clock):
    assert svc.valuation_quote("AAPL") is not None
    clock.advance(60)
    assert svc.valuation_quote("AAPL") is None


async def test_health_lists_stale(svc, clock):
    assert svc.health()["stale"] == []
    clock.advance(60)
    h = svc.health()
    assert h["market_source"] == "simulator" and sorted(h["stale"]) == ["AAPL", "TSLA"]
```

### 14.6 Contract suite

**`backend/tests/market/test_contract.py`**

```python
"""One suite, both sources: the behaviours downstream code relies on."""

from datetime import date

import pytest
from massive.rest.models import TickerSnapshot

from app.market.errors import UnknownTickerError
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource

NS = 1_760_649_600_000_000_000


class FakeClient:
    def get_snapshot_all(self, market_type, tickers):
        return [
            TickerSnapshot.from_dict(
                {"ticker": t, "lastTrade": {"p": 50.0, "t": NS}, "prevDay": {"c": 49.0}}
            )
            for t in tickers
            if t != "NOPE"
        ]

    def get_grouped_daily_aggs(self, on, adjusted=True):
        return []


@pytest.fixture(params=["simulator", "massive"])
def source(request, cache, clock):
    if request.param == "simulator":
        return SimulatorDataSource(cache, update_interval=10, seed=1, clock=clock)
    return MassiveDataSource(
        "k",
        cache,
        client=FakeClient(),
        clock=clock,
        poll_interval=3600,
        today=lambda: date(2025, 10, 17),
    )


async def test_start_stop_idempotent(source, cache):
    await source.start(["AAPL"])
    assert cache.get("AAPL") is not None
    await source.stop()
    await source.stop()


async def test_add_ticker_yields_cache_entry_or_raises(source, cache):
    await source.start(["AAPL"])
    await source.add_ticker("MSFT")
    assert cache.get("MSFT") is not None
    with pytest.raises(UnknownTickerError):
        await source.add_ticker("NOPE")
    await source.remove_ticker("MSFT")
    assert cache.get("MSFT") is None and "MSFT" not in source.get_tickers()
    await source.stop()


async def test_get_quotes_omits_unpriceable_never_zero(source):
    await source.start(["AAPL"])
    qs = await source.get_quotes(["AAPL", "NOPE"])
    assert "NOPE" not in qs and all(q.price > 0 for q in qs.values())
    await source.stop()
```

### 14.7 Manual checks

- **Live Massive smoke test** (needs a key; never in CI). Run once per plan type to settle the open items below:

  ```python
  # backend/scripts/massive_smoke.py  — usage: MASSIVE_API_KEY=... MASSIVE_PLAN=delayed uv run python scripts/massive_smoke.py
  import asyncio, os, time
  from app.market import PriceCache, create_market_data_source

  async def main():
      cache = PriceCache()
      src = create_market_data_source(cache)
      await src.start(["AAPL", "MSFT", "NVDA"])
      print("kind:", src.kind.value, "status:", src.status.value)
      for q in cache.get_all().values():
          print(q.ticker, q.price, "prev_close", q.prev_close, "as_of", time.ctime(q.as_of), q.source.value)
          assert time.time() - q.as_of < 5 * 86400, "as_of is not a plausible recent date (unit bug?)"
      print("get_quotes PYPL:", (await src.get_quotes(["PYPL"])).get("PYPL"))
      print("eod closes:", await src.get_eod_closes(["AAPL", "MSFT"]))
      await src.stop()

  asyncio.run(main())
  ```

- **Stream by hand**: `curl -N localhost:8000/api/stream/prices` shows `retry: 1000`, then a full snapshot, then one every ~0.5 s with the simulator.

## 15. Configuration reference

| Setting | Where | Default | Effect |
|---|---|---|---|
| `MASSIVE_API_KEY` | env | empty | non-empty → Massive; empty/whitespace/unset → simulator |
| `MASSIVE_PLAN` | env | `delayed` | `realtime` or `delayed` label (and poll rate) for snapshot quotes |
| `SIM_SEED` | env | unset | integer → deterministic simulator |
| `SIM_UPDATE_INTERVAL_S` | `config.py` | 0.5 | simulator tick |
| `SIM_EVENT_PROBABILITY` | `config.py` | 0.001 | shock chance per ticker per tick |
| `MASSIVE_POLL_INTERVAL_S` | `config.py` | realtime 3, delayed 15 | snapshot poll |
| `MASSIVE_BACKOFF_MAX_S` | `config.py` | 120 | cap on retry delay after failures |
| `MASSIVE_CHUNK_SIZE` | `config.py` | 250 | tickers per snapshot request |
| `EOD_REFRESH_S` / `EOD_RETRY_S` | `config.py` | 6 h / 60 s | EOD refresh and retry |
| `FRESH_LIMIT_S` | `config.py` | sim 5 s, realtime 30 s, delayed 75 s, EOD ∞ | max `fetched_at` age for trade/valuation |
| SSE interval / heartbeat | `stream.generate_events` | 0.5 s / 15 s | push cadence |

## 16. Implementation order

1. `errors.py`, `config.py`, `models.py`, `timeutil.py`, `tickers.py`, then `cache.py` and its tests. Nothing else compiles against the new types until these exist.
2. `interface.py`, `seed_prices.py`, `simulator.py` and tests. The simulator is the default path, so the rest of the app can be built on it.
3. `service.py`, `stream.py`, `factory.py`, `__init__.py` and tests; wire `main.py` per section 12.
4. `massive_client.py` and its tests (all offline, via `FakeClient`).
5. Update the demo script, `backend/CLAUDE.md` and `MARKET_DATA_SUMMARY.md`.
6. Run the live Massive smoke test with each plan type available, resolve section 17, and fold the answers into [MASSIVE_API.md](MASSIVE_API.md).

## 17. Open items

1. **Error bodies are unverified.** `classify_error` matches substrings of the JSON `status`/`message`/`error` fields (`NOT_AUTHORIZED`, "not entitled", "Unknown API Key", "maximum requests"). The SDK hides the HTTP status, so confirm the real bodies for 401, 403 and 429 with a live key. A wrong guess fails safe: an unrecognised body is `other`, which degrades and retries rather than switching modes.
2. **Starter plan without trades.** If its snapshots omit `lastTrade`, quotes come from `min.close`/`day.close`; confirm they are present and that `delayed` is the right label.
3. **Snapshot reset time.** Overnight, before the exchanges repopulate, tickers can arrive with only `prev_day`, giving `massive-eod` quotes. Confirm the reset window; behaviour is correct either way.
4. **Plan detection.** `MASSIVE_PLAN` is configuration, not detection, because the API does not label delayed vs real-time. Revisit if Massive exposes this.
5. **Simulator catalog prices** are illustrative round numbers; refresh them occasionally.
6. **Extending the catalog.** Adding a symbol means one `SEED_PRICES` entry; add it to a correlation group and `TICKER_PARAMS` if it should move with a sector.
