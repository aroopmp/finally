"""Factory for creating market data sources."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Choose the source from the environment.

    - MASSIVE_API_KEY set and non-empty -> MassiveDataSource (real market data)
    - otherwise -> SimulatorDataSource

    Returns an unstarted source. Caller must await source.start(tickers).
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        plan = os.environ.get("MASSIVE_PLAN", "delayed").strip().lower()
        logger.info("Market data source: Massive API (plan=%s)", plan)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, plan=plan)
    logger.info("Market data source: GBM Simulator")
    return SimulatorDataSource(price_cache=price_cache)
