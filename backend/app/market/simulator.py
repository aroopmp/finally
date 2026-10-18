"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import time
from collections.abc import Callable
from datetime import date

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .models import Quote, QuoteUnavailable, SourceKind, SourceStatus, UnknownTicker
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

logger = logging.getLogger(__name__)


class GBMSimulator:
    """Geometric Brownian Motion simulator for correlated stock prices.

    Math:
        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    Where:
        S(t)   = current price
        mu     = annualized drift (expected return)
        sigma  = annualized volatility
        dt     = time step as fraction of a trading year
        Z      = correlated standard normal random variable

    The tiny dt (~8.5e-8 for 500ms ticks over 252 trading days * 6.5h/day)
    produces sub-cent moves per tick that accumulate naturally over time.
    """

    # 500ms expressed as a fraction of a trading year
    # 252 trading days * 6.5 hours/day * 3600 seconds/hour = 5,896,800 seconds
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # ~8.48e-8

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability

        # Per-ticker state
        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}

        # Cholesky decomposition of the correlation matrix (for correlated moves)
        self._cholesky: np.ndarray | None = None

        # Initialize all starting tickers
        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    # --- Public API ---

    def step(self) -> dict[str, float]:
        """Advance all tickers by one time step. Returns {ticker: new_price}.

        This is the hot path — called every 500ms. Keep it fast.
        """
        n = len(self._tickers)
        if n == 0:
            return {}

        # Generate n independent standard normal draws
        z_independent = np.random.standard_normal(n)

        # Apply Cholesky to get correlated draws
        if self._cholesky is not None:
            z_correlated = self._cholesky @ z_independent
        else:
            z_correlated = z_independent

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            params = self._params[ticker]
            mu = params["mu"]
            sigma = params["sigma"]

            # GBM: S(t+dt) = S(t) * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z)
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z_correlated[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            # Random event: ~0.1% chance per tick per ticker
            # With 10 tickers at 2 ticks/sec, expect an event ~every 50 seconds
            if random.random() < self._event_prob:
                shock_magnitude = random.uniform(0.02, 0.05)
                shock_sign = random.choice([-1, 1])
                self._prices[ticker] *= 1 + shock_magnitude * shock_sign
                logger.debug(
                    "Random event on %s: %.1f%% %s",
                    ticker,
                    shock_magnitude * 100,
                    "up" if shock_sign > 0 else "down",
                )

            result[ticker] = round(self._prices[ticker], 2)

        return result

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the simulation. Rebuilds the correlation matrix."""
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the simulation. Rebuilds the correlation matrix."""
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        """Current price for a ticker, or None if not tracked."""
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        """Return the list of currently tracked tickers."""
        return list(self._tickers)

    # --- Internals ---

    def _add_ticker_internal(self, ticker: str) -> None:
        """Add a ticker without rebuilding Cholesky (for batch initialization)."""
        if ticker in self._prices:
            return
        self._tickers.append(ticker)
        self._prices[ticker] = SEED_PRICES.get(ticker, random.uniform(50.0, 300.0))
        self._params[ticker] = TICKER_PARAMS.get(ticker, dict(DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """Rebuild the Cholesky decomposition of the ticker correlation matrix.

        Called whenever tickers are added or removed. O(n^2) but n < 50.
        """
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return

        # Build the correlation matrix
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = rho
                corr[j, i] = rho

        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        """Determine correlation between two tickers based on sector grouping.

        Correlation structure:
          - Same tech sector:   0.6
          - Same finance sector: 0.5
          - TSLA with anything: 0.3 (it does its own thing)
          - Cross-sector:       0.3
          - Unknown tickers:    0.3
        """
        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]

        # TSLA is in tech set but behaves independently
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR

        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR

        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by the GBM simulator.

    Runs a background asyncio task that calls GBMSimulator.step() every
    `update_interval` seconds and writes results to the PriceCache. The first
    price seen for a ticker is its `prev_close` baseline ("change since start").

    Only tickers with a seed price are supported; others raise UnknownTicker.
    """

    kind = SourceKind.SIMULATOR

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        seed: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._seed = seed if seed is not None else _env_seed()
        self._clock = clock
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None
        self.status = SourceStatus.STARTING

    @staticmethod
    def supports(ticker: str) -> bool:
        return ticker in SEED_PRICES

    async def start(self, tickers: list[str]) -> None:
        if self._seed is not None:
            np.random.seed(self._seed)
            random.seed(self._seed)
        tickers = [t for t in tickers if self.supports(t)]
        # dt scales with the tick interval so annual volatility stays right
        dt = self._interval / GBMSimulator.TRADING_SECONDS_PER_YEAR
        self._sim = GBMSimulator(tickers=tickers, dt=dt, event_probability=self._event_prob)
        for ticker in tickers:
            self._seed_cache(ticker)
        self.status = SourceStatus.OK
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(tickers))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        if not self.supports(ticker):
            raise UnknownTicker(f"{ticker} is not a supported ticker")
        if self._sim is None:
            raise QuoteUnavailable("simulator not started")
        if ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._seed_cache(ticker)
        logger.info("Simulator: added ticker %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    async def get_quotes(self, tickers: list[str]) -> dict[str, Quote]:
        """Current quotes. Untracked supported tickers get a static seed-price quote."""
        out: dict[str, Quote] = {}
        for ticker in tickers:
            quote = self._cache.get(ticker)
            if quote is None and self.supports(ticker):
                now = self._clock()
                quote = Quote(ticker, SEED_PRICES[ticker], SEED_PRICES[ticker],
                              SEED_PRICES[ticker], now, now, self.kind)
            if quote is not None:
                out[ticker] = quote
        return out

    async def get_eod_closes(
        self, tickers: list[str], on: date | None = None
    ) -> dict[str, float]:
        """The simulator has no history: returns the session baseline (seed) price."""
        return {t: SEED_PRICES[t] for t in tickers if self.supports(t)}

    def _seed_cache(self, ticker: str) -> None:
        assert self._sim is not None
        price = self._sim.get_price(ticker)
        if price is not None:
            self._cache.update(
                ticker, price, as_of=self._clock(), prev_close=SEED_PRICES[ticker], source=self.kind
            )

    async def _run_loop(self) -> None:
        """Core loop: step the simulation, write to cache, sleep."""
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker, price, as_of=self._clock(), source=self.kind)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)


def _env_seed() -> int | None:
    raw = os.environ.get("SIM_SEED", "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else None
