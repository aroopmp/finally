"""Shared application state (one per FastAPI app)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from .market import MarketDataSource, PriceCache


@dataclass
class AppState:
    db_path: Path
    cache: PriceCache
    source: MarketDataSource
    # Serialises every mutation (trade, watchlist change, chat execution, reset).
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Bumped on reset so in-flight chat requests from the old account are rejected.
    epoch: int = 0
