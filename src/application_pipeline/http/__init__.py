from __future__ import annotations

import time
from typing import Any, Callable

from .retry import HttpNotRetryableError, HttpRetryError, exponential_backoff, retry

__all__ = [
    "HttpPost",
    "HttpRetryError",
    "HttpNotRetryableError",
    "post_with_retries",
]

HttpPost = Callable[[str, dict[str, Any], float], dict[str, Any]]

_BACKOFF_INITIAL = 1.0
_BACKOFF_MULTIPLIER = 2.0
_BACKOFF_MAX = 8.0


def post_with_retries(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    retries: int,
    http_post: HttpPost,
    *,
    _sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    return retry(
        lambda: http_post(url, payload, timeout),
        predicate=lambda exc: not isinstance(exc, HttpNotRetryableError),
        backoff_policy=exponential_backoff(
            _BACKOFF_INITIAL, _BACKOFF_MULTIPLIER, _BACKOFF_MAX
        ),
        max_retries=retries,
        error_factory=lambda n, exc: HttpRetryError(
            f"HTTP request failed after {n} retries: {exc}"
        ),
        _sleep=_sleep,
    )
