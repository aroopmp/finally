"""Thread-safe in-memory price cache."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from threading import Lock

from .models import Quote, SourceKind


class PriceCache:
    """Latest quote per ticker.

    Writers: SimulatorDataSource or MassiveDataSource (one at a time).
    Readers: SSE stream, portfolio valuation, trade execution.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._quotes: dict[str, Quote] = {}
        self._baselines: dict[str, float] = {}
        self._lock = Lock()
        self._version = 0
        self._clock = clock

    def update(
        self,
        ticker: str,
        price: float,
        *,
        as_of: float | None = None,
        prev_close: float | None = None,
        source: SourceKind = SourceKind.SIMULATOR,
    ) -> Quote | None:
        """Record a price. Returns the Quote, or None if the price was rejected.

        Prices that are missing, non-finite or <= 0 are rejected and the old
        quote is kept. `prev_close` is remembered per ticker and carried forward.
        """
        if price is None or not math.isfinite(price) or price <= 0:
            return None
        with self._lock:
            now = self._clock()
            prev = self._quotes.get(ticker)
            if prev_close is not None and math.isfinite(prev_close) and prev_close > 0:
                self._baselines[ticker] = round(prev_close, 2)
            price = round(price, 2)
            quote = Quote(
                ticker=ticker,
                price=price,
                prev_price=prev.price if prev else price,
                prev_close=self._baselines.get(ticker),
                as_of=as_of if as_of else now,
                fetched_at=now,
                source=source,
            )
            self._quotes[ticker] = quote
            self._version += 1
            return quote

    def get(self, ticker: str) -> Quote | None:
        with self._lock:
            return self._quotes.get(ticker)

    def get_fresh(self, ticker: str, max_age: float | None) -> Quote | None:
        """The quote, or None if missing or older than max_age seconds (None = never stale)."""
        quote = self.get(ticker)
        if quote is None:
            return None
        if max_age is not None and quote.age(self._clock()) > max_age:
            return None
        return quote

    def get_all(self) -> dict[str, Quote]:
        with self._lock:
            return dict(self._quotes)

    def get_price(self, ticker: str) -> float | None:
        quote = self.get(ticker)
        return quote.price if quote else None

    def remove(self, ticker: str) -> None:
        with self._lock:
            existed = self._quotes.pop(ticker, None) is not None
            self._baselines.pop(ticker, None)
            if existed:
                self._version += 1

    def clear(self) -> None:
        with self._lock:
            self._quotes.clear()
            self._baselines.clear()
            self._version += 1

    @property
    def version(self) -> int:
        return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._quotes)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._quotes
