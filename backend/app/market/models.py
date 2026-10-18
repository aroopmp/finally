"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum


class SourceKind(str, Enum):
    SIMULATOR = "simulator"
    MASSIVE_REALTIME = "massive-realtime"
    MASSIVE_DELAYED = "massive-delayed"  # ~15 minutes old
    MASSIVE_EOD = "massive-eod"  # last close only (free plan)


class SourceStatus(str, Enum):
    STARTING = "starting"  # no successful fetch yet
    OK = "ok"
    DEGRADED = "degraded"  # last fetch failed, serving older quotes
    ERROR = "error"  # unrecoverable, e.g. bad API key


class UnknownTicker(ValueError):
    """The ticker is not in the supported universe / cannot be priced."""


class QuoteUnavailable(RuntimeError):
    """No usable (present and fresh) price is available for the ticker."""


@dataclass(frozen=True, slots=True)
class Quote:
    """Immutable price snapshot of a single ticker."""

    ticker: str
    price: float  # positive, finite
    prev_price: float  # previous tick; drives the green/red flash
    prev_close: float | None  # daily-change baseline; None if unknown
    as_of: float  # unix SECONDS when the price was struck
    fetched_at: float  # unix seconds when we stored it
    source: SourceKind

    @property
    def change(self) -> float:
        """Absolute change from the previous tick."""
        return round(self.price - self.prev_price, 4)

    @property
    def direction(self) -> str:
        """'up', 'down' or 'flat' versus the previous tick."""
        if self.price > self.prev_price:
            return "up"
        if self.price < self.prev_price:
            return "down"
        return "flat"

    @property
    def day_change(self) -> float | None:
        if self.prev_close is None:
            return None
        return round(self.price - self.prev_close, 4)

    @property
    def day_change_pct(self) -> float | None:
        if not self.prev_close:
            return None
        return round((self.price - self.prev_close) / self.prev_close * 100, 4)

    def age(self, now: float | None = None) -> float:
        return (time.time() if now is None else now) - self.as_of

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "prev_price": self.prev_price,
            "prev_close": self.prev_close,
            "change": self.change,
            "direction": self.direction,
            "day_change": self.day_change,
            "day_change_pct": self.day_change_pct,
            "as_of": self.as_of,
            "source": self.source.value,
        }
