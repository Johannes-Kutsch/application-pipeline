import time
from typing import Callable

from application_pipeline.http import HttpRetryError

DEFAULT_TIMEOUT: float = 30.0
DEFAULT_RETRIES: int = 3
THROTTLE_INTERVAL: float = 0.5

HttpGet = Callable[[str, float], bytes]


class Throttle:
    """Enforces a minimum interval between calls — one instance per parser, one host."""

    def __init__(
        self,
        interval: float = THROTTLE_INTERVAL,
        _now: Callable[[], float] = time.monotonic,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = interval
        self._now = _now
        self._sleep = _sleep
        self._last: float = float("-inf")

    def wait(self) -> None:
        now = self._now()
        elapsed = now - self._last
        if elapsed < self._interval:
            self._sleep(self._interval - elapsed)
        self._last = now


def request_with_retry(
    url: str,
    timeout: float,
    retries: int,
    http_get: HttpGet,
) -> bytes:
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            return http_get(url, timeout)
        except Exception as exc:
            last_exc = exc
    raise HttpRetryError(
        f"HTTP request failed after {retries} retries: {last_exc}"
    ) from last_exc
