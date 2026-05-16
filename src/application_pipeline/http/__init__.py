from __future__ import annotations

from .errors import HttpNotRetryableError, HttpRetryError

__all__ = [
    "HttpRetryError",
    "HttpNotRetryableError",
]
