"""Timestamp normalisation for provider data."""

from __future__ import annotations

import math


def to_unix_seconds(ts: int | float | None) -> float | None:
    """Convert a Massive timestamp to Unix seconds.

    Massive uses nanoseconds for trades/quotes/`updated` and milliseconds for
    aggregates and minute bars. The magnitude check disambiguates them.
    """
    if ts is None or isinstance(ts, bool) or not math.isfinite(ts) or ts <= 0:
        return None
    if ts >= 1e17:  # nanoseconds
        return ts / 1e9
    if ts >= 1e11:  # milliseconds
        return ts / 1e3
    return float(ts)
