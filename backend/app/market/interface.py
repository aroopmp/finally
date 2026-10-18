"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from .models import Quote, SourceKind, SourceStatus

# Max quote age (seconds) accepted for trading and valuation. None = never stale
# within the session day (end-of-day data is labelled "last close" instead).
FRESH_LIMIT: dict[SourceKind, float | None] = {
    SourceKind.SIMULATOR: 5.0,
    SourceKind.MASSIVE_REALTIME: 60.0,
    SourceKind.MASSIVE_DELAYED: 20 * 60.0,
    SourceKind.MASSIVE_EOD: None,
}


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push quotes into a shared PriceCache on their own schedule.
    Downstream code reads the cache (or uses the one-shot methods) and never
    talks to a provider directly.
    """

    kind: SourceKind
    status: SourceStatus = SourceStatus.STARTING

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing quotes. Call once; the first fetch completes before returning."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop background work. Safe to call repeatedly."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Track a ticker. Returns only once it has a cache entry, or raises
        UnknownTicker / QuoteUnavailable. No-op if already tracked."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and drop it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers."""

    @abstractmethod
    async def get_quotes(self, tickers: list[str]) -> dict[str, Quote]:
        """Latest quote per ticker. Tickers that cannot be priced are omitted (never zero)."""

    @abstractmethod
    async def get_eod_closes(
        self, tickers: list[str], on: date | None = None
    ) -> dict[str, float]:
        """Closing price per ticker for `on` (default: most recent completed trading day)."""

    def max_quote_age(self) -> float | None:
        return FRESH_LIMIT[self.kind]
