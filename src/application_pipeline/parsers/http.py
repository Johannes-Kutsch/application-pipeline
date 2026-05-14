from __future__ import annotations

import time
from typing import Callable

import httpx

from application_pipeline import parser_log
from application_pipeline._context import current_stage
from application_pipeline.http import HttpRetryError
from application_pipeline.http.retry import (
    HttpNotRetryableError,
    exponential_backoff,
)

from ._http import (
    BACKOFF_INITIAL,
    BACKOFF_MAX,
    BACKOFF_MULTIPLIER,
    HTTP_CONNECT_TIMEOUT,
    HTTP_READ_TIMEOUT,
    MAX_RETRIES,
    REQUEST_PACING,
    RETRY_STATUSES,
    USER_AGENT,
)

# Backward-compatible aliases kept so existing callers compile without changes.
DEFAULT_TIMEOUT = HTTP_READ_TIMEOUT
DEFAULT_RETRIES = MAX_RETRIES
THROTTLE_INTERVAL = REQUEST_PACING

HttpGet = Callable[[str, float], bytes]


def check_response_status(resp: httpx.Response, url: str) -> None:
    """Raise an appropriate error for non-2xx responses.

    Retryable statuses (RETRY_STATUSES) raise httpx.HTTPStatusError so the
    retry loop can pick them up.  All other non-2xx statuses raise
    HttpNotRetryableError with a tagged prefix so retries are skipped.
    """
    if resp.is_success:
        return
    status = resp.status_code
    if status == 404:
        raise HttpNotRetryableError(f"not found: {url}")
    if status in (401, 403):
        raise HttpNotRetryableError(f"auth: {url} status={status}")
    if status in (400, 422):
        raise HttpNotRetryableError(f"malformed: {url} status={status}")
    if 500 <= status < 600 and status not in RETRY_STATUSES:
        raise HttpNotRetryableError(f"upstream: {url} status={status}")
    # Retryable statuses (429, 502, 503, 504) and any unclassified codes
    # become httpx.HTTPStatusError so the retry predicate allows them.
    resp.raise_for_status()


def _default_http_get(url: str, timeout: float) -> bytes:
    with httpx.Client(
        timeout=httpx.Timeout(HTTP_READ_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT),
        headers={"User-Agent": USER_AGENT},
    ) as client:
        resp = client.get(url, timeout=timeout)
        check_response_status(resp, url)
        return resp.content


class Throttle:
    """Enforces a minimum interval between calls — one instance per parser, one host."""

    def __init__(
        self,
        interval: float = REQUEST_PACING,
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


def _log_component_id() -> str:
    stage = current_stage.get()
    return stage.removeprefix("parser:")


def request_with_retry(
    url: str,
    timeout: float,
    retries: int,
    http_get: HttpGet,
    *,
    _sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    component_id = _log_component_id()
    backoff = exponential_backoff(BACKOFF_INITIAL, BACKOFF_MULTIPLIER, BACKOFF_MAX)
    last_exc: Exception | None = None

    for attempt in range(retries):
        parser_log.record(component_id, "http_get_start", url=url, attempt=attempt + 1)
        t0 = time.monotonic()
        try:
            result = http_get(url, timeout)
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            parser_log.record(
                component_id,
                "http_get_ok",
                url=url,
                bytes=len(result),
                elapsed_ms=elapsed_ms,
            )
            return result
        except HttpNotRetryableError:
            raise
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            last_exc = exc
            if attempt < retries - 1:
                parser_log.record(
                    component_id,
                    "http_get_retry",
                    url=url,
                    attempt=attempt + 1,
                    reason=str(exc),
                    elapsed_ms=elapsed_ms,
                )
                _sleep(backoff(attempt))

    raise HttpRetryError(
        f"HTTP request failed after {retries} retries: {last_exc}"
    ) from last_exc
