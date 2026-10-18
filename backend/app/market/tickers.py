"""Ticker normalisation and validation shared by every input path."""

from __future__ import annotations

import re

from .models import UnknownTicker

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


def normalize_ticker(raw: str) -> str:
    """Trim, uppercase and validate format (1-5 letters). Raises UnknownTicker."""
    ticker = (raw or "").strip().upper()
    if not _TICKER_RE.match(ticker):
        raise UnknownTicker(f"Invalid ticker {raw!r}: expected 1-5 letters")
    return ticker
