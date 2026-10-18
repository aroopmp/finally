"""Market data subsystem for FinAlly (see planning/MARKET_INTERFACE.md).

Public API:
    Quote, SourceKind, SourceStatus - price model and enums
    PriceCache                      - thread-safe in-memory quote store
    MarketDataSource                - abstract interface for data providers
    create_market_data_source       - factory: Massive if MASSIVE_API_KEY set, else simulator
    create_stream_router            - FastAPI router factory for the SSE endpoint
"""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import FRESH_LIMIT, MarketDataSource
from .models import Quote, QuoteUnavailable, SourceKind, SourceStatus, UnknownTicker
from .stream import create_stream_router
from .tickers import normalize_ticker

__all__ = [
    "FRESH_LIMIT",
    "MarketDataSource",
    "PriceCache",
    "Quote",
    "QuoteUnavailable",
    "SourceKind",
    "SourceStatus",
    "UnknownTicker",
    "create_market_data_source",
    "create_stream_router",
    "normalize_ticker",
]
