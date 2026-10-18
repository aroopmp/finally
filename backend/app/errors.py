"""API error type and mapping from domain errors."""

from __future__ import annotations


class ApiError(Exception):
    """Rendered as {"error": {"code": ..., "message": ...}} with the given HTTP status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
