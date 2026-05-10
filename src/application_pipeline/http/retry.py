from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


class HttpRetryError(Exception):
    """All retries exhausted."""


class HttpNotRetryableError(Exception):
    """HTTP error that must not be retried (e.g. 404, auth failure)."""


def exponential_backoff(
    initial: float, multiplier: float, cap: float
) -> Callable[[int], float]:
    """Returns a backoff policy: attempt index → sleep seconds."""

    def policy(attempt: int) -> float:
        return min(initial * (multiplier**attempt), cap)

    return policy


def retry(
    fn: Callable[[], T],
    *,
    predicate: Callable[[Exception], bool],
    backoff_policy: Callable[[int], float],
    max_retries: int,
    error_factory: Callable[[int, Exception | None], Exception],
    _sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry *fn* up to *max_retries* times when *predicate(exc)* is True.

    Sleeps *backoff_policy(attempt)* seconds between retries.
    Raises *error_factory(max_retries, last_exc)* when retries are exhausted.
    Raises *error_factory(0, None)* immediately when *max_retries* <= 0.
    Non-retryable exceptions (predicate returns False) propagate immediately.
    """
    if max_retries <= 0:
        raise error_factory(0, None)

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            if not predicate(exc):
                raise
            last_exc = exc
            if attempt < max_retries - 1:
                _sleep(backoff_policy(attempt))

    assert last_exc is not None
    raise error_factory(max_retries, last_exc) from last_exc
