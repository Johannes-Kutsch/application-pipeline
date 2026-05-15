from __future__ import annotations

from .retry import HttpNotRetryableError, HttpRetryError

__all__ = [
    "HttpRetryError",
    "HttpNotRetryableError",
]
