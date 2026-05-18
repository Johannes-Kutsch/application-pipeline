from __future__ import annotations

import time
from collections.abc import Callable, Mapping

import httpx

from application_pipeline import parser_log
from application_pipeline.http import HttpNotRetryableError, HttpRetryError
from application_pipeline.parsers.errors import ParserError

HTTP_CONNECT_TIMEOUT: float = 5.0
HTTP_READ_TIMEOUT: float = 30.0
MAX_RETRIES: int = 3
BACKOFF_INITIAL: float = 1.0
BACKOFF_MULTIPLIER: float = 2.0
BACKOFF_MAX: float = 8.0
RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
REQUEST_PACING: float = 0.5
USER_AGENT: str = "application-pipeline/0.1 (job-discovery-bot)"


class _Throttle:
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


class ParserHttp:
    """Owns one httpx.Client, one _Throttle, the retry loop, and error wrapping."""

    def __init__(
        self,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = HTTP_READ_TIMEOUT,
        retries: int = MAX_RETRIES,
        _http_get: Callable[[str, float], bytes] | None = None,
        _throttle: _Throttle | None = None,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        merged_headers: dict[str, str] = {"User-Agent": USER_AGENT, **(headers or {})}
        self._timeout = timeout
        self._retries = retries
        self._sleep = _sleep
        self._throttle = _throttle if _throttle is not None else _Throttle()
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=HTTP_CONNECT_TIMEOUT),
            headers=merged_headers,
        )
        self._http_get: Callable[[str, float], bytes] = (
            _http_get if _http_get is not None else self._real_http_get
        )

    @staticmethod
    def _check_response_status(resp: httpx.Response, url: str) -> None:
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
        resp.raise_for_status()

    def _real_http_get(self, url: str, timeout: float) -> bytes:
        resp = self._client.get(url, timeout=timeout)
        self._check_response_status(resp, url)
        return resp.content

    def _get_with_retry(self, url: str) -> bytes:
        component_id = "parser_http"

        if self._retries <= 0:
            raise HttpRetryError("HTTP request failed after 0 retries: None")

        last_exc: Exception | None = None
        for attempt in range(self._retries):
            attempt_num = attempt + 1
            parser_log.record(
                component_id, "http_get_start", url=url, attempt=attempt_num
            )
            t_start = time.monotonic()
            try:
                result = self._http_get(url, self._timeout)
            except HttpNotRetryableError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < self._retries - 1:
                    elapsed_ms = round((time.monotonic() - t_start) * 1000)
                    parser_log.record(
                        component_id,
                        "http_get_retry",
                        url=url,
                        attempt=attempt_num,
                        reason=str(exc),
                        elapsed_ms=elapsed_ms,
                    )
                    self._sleep(
                        min(BACKOFF_INITIAL * BACKOFF_MULTIPLIER**attempt, BACKOFF_MAX)
                    )
                continue
            elapsed_ms = round((time.monotonic() - t_start) * 1000)
            parser_log.record(
                component_id,
                "http_get_ok",
                url=url,
                bytes=len(result),
                elapsed_ms=elapsed_ms,
            )
            return result

        assert last_exc is not None
        raise HttpRetryError(
            f"HTTP request failed after {self._retries} retries: {last_exc}"
        ) from last_exc

    def get(self, url: str, *, error_prefix: str) -> bytes:
        self._throttle.wait()
        try:
            return self._get_with_retry(url)
        except HttpRetryError as exc:
            raise ParserError(f"{error_prefix}: {exc}") from exc.__cause__

    def __enter__(self) -> ParserHttp:
        return self

    def __exit__(self, *args: object) -> None:
        self._client.close()
