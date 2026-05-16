from __future__ import annotations

import time
from typing import Callable

import httpx

from application_pipeline import parser_log
from application_pipeline._context import current_stage
from application_pipeline.http import HttpNotRetryableError, HttpRetryError

HTTP_CONNECT_TIMEOUT: float = 5.0
HTTP_READ_TIMEOUT: float = 30.0
MAX_RETRIES: int = 3
BACKOFF_INITIAL: float = 1.0
BACKOFF_MULTIPLIER: float = 2.0
BACKOFF_MAX: float = 8.0
RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
REQUEST_PACING: float = 0.5
USER_AGENT: str = "application-pipeline/0.1 (job-discovery-bot)"

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

    if retries <= 0:
        raise HttpRetryError("HTTP request failed after 0 retries: None")

    last_exc: Exception | None = None
    t_start = 0.0
    for attempt in range(retries):
        attempt_num = attempt + 1
        try:
            parser_log.record(
                component_id, "http_get_start", url=url, attempt=attempt_num
            )
            t_start = time.monotonic()
            result = http_get(url, timeout)
            elapsed_ms = round((time.monotonic() - t_start) * 1000)
            parser_log.record(
                component_id,
                "http_get_ok",
                url=url,
                bytes=len(result),
                elapsed_ms=elapsed_ms,
            )
            return result
        except Exception as exc:
            if isinstance(exc, HttpNotRetryableError):
                raise
            last_exc = exc
            if attempt < retries - 1:
                elapsed_ms = round((time.monotonic() - t_start) * 1000)
                parser_log.record(
                    component_id,
                    "http_get_retry",
                    url=url,
                    attempt=attempt_num,
                    reason=str(exc),
                    elapsed_ms=elapsed_ms,
                )
                _sleep(min(BACKOFF_INITIAL * BACKOFF_MULTIPLIER**attempt, BACKOFF_MAX))

    assert last_exc is not None
    raise HttpRetryError(
        f"HTTP request failed after {retries} retries: {last_exc}"
    ) from last_exc
