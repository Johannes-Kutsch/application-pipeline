from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx

from application_pipeline.http import (
    HttpNotRetryableError,
    HttpParserFatalError,
    HttpRedirectResponse,
    HttpRetryError,
    HttpStubNotRetryableError,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.errors import ParserError
from application_pipeline.parsers.types import EnrichFailedError

HTTP_CONNECT_TIMEOUT: float = 5.0
HTTP_READ_TIMEOUT: float = 30.0
MAX_RETRIES: int = 3
BACKOFF_INITIAL: float = 1.0
BACKOFF_MULTIPLIER: float = 2.0
BACKOFF_MAX: float = 8.0
RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
REQUEST_PACING: float = 0.5
USER_AGENT: str = "application-pipeline/0.1 (job-discovery-bot)"


class ParserHttpTransport(Protocol):
    def get(self, url: str, *, timeout: float) -> httpx.Response: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ScriptedParserHttpRequest:
    url: str
    timeout: float


@dataclass(frozen=True)
class ScriptedParserHttpResponse:
    status: int
    content: bytes = b""
    headers: Mapping[str, str] | None = None

    @classmethod
    def redirect(cls, *, status: int, location: str) -> ScriptedParserHttpResponse:
        return cls(status=status, headers={"location": location})


ScriptedParserHttpOutcome = bytes | Exception | ScriptedParserHttpResponse


class ScriptedParserHttpTransport:
    def __init__(self, outcomes: list[ScriptedParserHttpOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[ScriptedParserHttpRequest] = []
        self._closed = False

    def get(self, url: str, *, timeout: float) -> httpx.Response:
        if self._closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        self.requests.append(ScriptedParserHttpRequest(url=url, timeout=timeout))
        if not self._outcomes:
            raise AssertionError("ScriptedParserHttpTransport ran out of outcomes")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        request = httpx.Request("GET", url)
        if isinstance(outcome, bytes):
            return httpx.Response(200, content=outcome, request=request)
        return httpx.Response(
            outcome.status,
            content=outcome.content,
            headers=outcome.headers,
            request=request,
        )

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> ScriptedParserHttpTransport:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class HttpxParserHttpTransport:
    def __init__(
        self,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = HTTP_READ_TIMEOUT,
    ) -> None:
        merged_headers: dict[str, str] = {"User-Agent": USER_AGENT, **(headers or {})}
        self._client = httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(timeout, connect=HTTP_CONNECT_TIMEOUT),
            headers=merged_headers,
        )

    @property
    def client(self) -> httpx.Client:
        return self._client

    def get(self, url: str, *, timeout: float) -> httpx.Response:
        return self._client.get(
            url,
            timeout=httpx.Timeout(timeout, connect=HTTP_CONNECT_TIMEOUT),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpxParserHttpTransport:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


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
    """Owns one transport, one _Throttle, the retry loop, and error wrapping."""

    def __init__(
        self,
        *,
        run_log: RunLog,
        headers: Mapping[str, str] | None = None,
        timeout: float = HTTP_READ_TIMEOUT,
        retries: int = MAX_RETRIES,
        _http_get: Callable[[str, float], bytes] | None = None,
        _transport: ParserHttpTransport | None = None,
        _throttle: _Throttle | None = None,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._run_log = run_log
        self._timeout = timeout
        self._retries = retries
        self._sleep = _sleep
        self._throttle = (
            _throttle if _throttle is not None else _Throttle(_sleep=_sleep)
        )
        self._transport = (
            _transport
            if _transport is not None
            else HttpxParserHttpTransport(headers=headers, timeout=timeout)
        )
        self._native_client = (
            self._transport.client
            if isinstance(self._transport, HttpxParserHttpTransport)
            else None
        )
        self._http_get: Callable[[str, float], bytes] = (
            _http_get if _http_get is not None else self._real_http_get
        )

    @property
    def _client(self) -> httpx.Client:
        if self._native_client is None:
            raise AttributeError("ParserHttp transport does not expose an httpx.Client")
        return self._native_client

    def _real_http_get(self, url: str, timeout: float) -> bytes:
        resp = self._transport.get(url, timeout=timeout)
        if resp.is_success:
            return resp.content
        status = resp.status_code
        if 300 <= status < 400:
            location = resp.headers.get("location", "")
            self._run_log.event(
                "parser_http",
                "http_get_redirect",
                url=url,
                status=status,
                location=location,
            )
            raise HttpRedirectResponse(status, location)
        classified = self._classify_failure(status, url)
        if classified is None:
            # Retryable — let httpx raise so the retry loop handles it.
            resp.raise_for_status()
            return resp.content  # unreachable
        reason, fatal = classified
        event = "http_get_fatal" if fatal else "http_get_skipped"
        self._run_log.event("parser_http", event, url=url, status=status, reason=reason)
        raise (HttpParserFatalError if fatal else HttpStubNotRetryableError)(reason)

    @staticmethod
    def _classify_failure(status: int, url: str) -> tuple[str, bool] | None:
        """Returns (reason, fatal) for non-retryable statuses, or None if retryable."""
        if status == 404:
            return f"not found: {url}", False
        if status in (400, 422):
            return f"malformed: {url} status={status}", False
        if status in (401, 403):
            return f"auth: {url} status={status}", True
        if 500 <= status < 600 and status not in RETRY_STATUSES:
            return f"upstream: {url} status={status}", True
        if status not in RETRY_STATUSES and not (300 <= status < 400):
            return f"unexpected: {url} status={status}", True
        return None

    def _get_with_retry(self, url: str) -> bytes:
        component_id = "parser_http"

        if self._retries <= 0:
            raise HttpRetryError("HTTP request failed after 0 retries: None")

        last_exc: Exception | None = None
        for attempt in range(self._retries):
            attempt_num = attempt + 1
            self._run_log.event(
                component_id, "http_get_start", url=url, attempt=attempt_num
            )
            t_start = time.monotonic()
            try:
                result = self._http_get(url, self._timeout)
            except (HttpRedirectResponse, HttpNotRetryableError):
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < self._retries - 1:
                    elapsed_ms = round((time.monotonic() - t_start) * 1000)
                    self._run_log.event(
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
            self._run_log.event(
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

    def enrich_get(self, url: str, *, error_prefix: str) -> bytes:
        """Like get(), but converts HttpStubNotRetryableError to EnrichFailedError."""
        self._throttle.wait()
        try:
            return self._get_with_retry(url)
        except HttpRetryError as exc:
            raise ParserError(f"{error_prefix}: {exc}") from exc.__cause__
        except HttpStubNotRetryableError as exc:
            raise EnrichFailedError(f"{error_prefix}: {exc}") from exc

    def get(self, url: str, *, error_prefix: str) -> bytes:
        self._throttle.wait()
        try:
            return self._get_with_retry(url)
        except HttpRetryError as exc:
            raise ParserError(f"{error_prefix}: {exc}") from exc.__cause__
        except HttpStubNotRetryableError as exc:
            raise ParserError(f"{error_prefix}: {exc}") from exc

    def __enter__(self) -> ParserHttp:
        return self

    def close(self) -> None:
        self._transport.close()

    def __exit__(self, *args: object) -> None:
        self.close()
