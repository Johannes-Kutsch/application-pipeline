"""Tests for the ParserHttp class interface in parsers/http.py."""

from __future__ import annotations

import httpx
import pytest
import respx

import application_pipeline.parser_log as parser_log
from application_pipeline.http import HttpNotRetryableError
from application_pipeline.parsers.errors import ParserError
from application_pipeline.parsers.http import (
    USER_AGENT,
    ParserHttp,
    Throttle,
)

_NO_SLEEP = lambda _: None  # noqa: E731


@pytest.fixture(autouse=True)
def reset_parser_log():
    parser_log._logs_dir = None
    yield
    parser_log._logs_dir = None


@pytest.fixture
def log_dir(tmp_path):
    parser_log.configure(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_get_returns_bytes_on_success():
    def http_get(url: str, timeout: float) -> bytes:
        return b"hello"

    parser = ParserHttp(_http_get=http_get, _sleep=_NO_SLEEP)
    assert parser.get("http://example.com/", error_prefix="test") == b"hello"


# ---------------------------------------------------------------------------
# Retry-then-success
# ---------------------------------------------------------------------------


def test_get_retries_then_returns_bytes_on_second_attempt():
    attempt = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise OSError("timeout")
        return b"ok"

    parser = ParserHttp(retries=2, _http_get=http_get, _sleep=_NO_SLEEP)
    result = parser.get("http://example.com/", error_prefix="test")
    assert result == b"ok"
    assert attempt == 2


# ---------------------------------------------------------------------------
# Retry exhaustion → wrapped ParserError
# ---------------------------------------------------------------------------


def test_get_raises_parser_error_after_retry_exhaustion():
    def http_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    parser = ParserHttp(retries=2, _http_get=http_get, _sleep=_NO_SLEEP)
    with pytest.raises(ParserError):
        parser.get("http://example.com/", error_prefix="myparser")


def test_get_parser_error_message_includes_error_prefix():
    def http_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    parser = ParserHttp(retries=1, _http_get=http_get, _sleep=_NO_SLEEP)
    with pytest.raises(ParserError, match="myprefix"):
        parser.get("http://example.com/", error_prefix="myprefix")


def test_get_parser_error_chains_to_underlying_cause():
    cause = OSError("connection refused")

    def http_get(url: str, timeout: float) -> bytes:
        raise cause

    parser = ParserHttp(retries=1, _http_get=http_get, _sleep=_NO_SLEEP)
    with pytest.raises(ParserError) as exc_info:
        parser.get("http://example.com/", error_prefix="p")

    assert exc_info.value.__cause__ is cause


# ---------------------------------------------------------------------------
# Non-retryable statuses → unwrapped HttpNotRetryableError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exc_msg", ["not found:", "auth:", "malformed:", "upstream:"])
def test_get_propagates_not_retryable_error_unwrapped(exc_msg):
    def http_get(url: str, timeout: float) -> bytes:
        raise HttpNotRetryableError(exc_msg)

    parser = ParserHttp(retries=3, _http_get=http_get, _sleep=_NO_SLEEP)
    with pytest.raises(HttpNotRetryableError):
        parser.get("http://example.com/", error_prefix="p")


def test_get_not_retryable_is_not_wrapped_in_parser_error():
    def http_get(url: str, timeout: float) -> bytes:
        raise HttpNotRetryableError("not found: url")

    parser = ParserHttp(retries=3, _http_get=http_get, _sleep=_NO_SLEEP)
    with pytest.raises(Exception) as exc_info:
        parser.get("http://example.com/", error_prefix="p")
    assert not isinstance(exc_info.value, ParserError)


def test_get_not_retryable_fires_on_first_attempt_without_further_tries():
    calls = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        raise HttpNotRetryableError("auth: url")

    parser = ParserHttp(retries=3, _http_get=http_get, _sleep=_NO_SLEEP)
    with pytest.raises(HttpNotRetryableError):
        parser.get("http://example.com/", error_prefix="p")
    assert calls == 1


# ---------------------------------------------------------------------------
# Retryable statuses traverse the retry loop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_get_retries_on_retryable_status_codes(status: int):
    req = httpx.Request("GET", "http://example.com/")
    resp = httpx.Response(status, request=req)
    attempt = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal attempt
        attempt += 1
        if attempt < 2:
            raise httpx.HTTPStatusError(f"{status}", request=req, response=resp)
        return b"ok"

    parser = ParserHttp(retries=3, _http_get=http_get, _sleep=_NO_SLEEP)
    result = parser.get("http://example.com/", error_prefix="p")
    assert result == b"ok"
    assert attempt == 2


# ---------------------------------------------------------------------------
# Throttle pacing across two get() calls
# ---------------------------------------------------------------------------


def test_throttle_pacing_between_consecutive_get_calls():
    sleeps: list[float] = []
    now_calls = iter([0.0, 0.1, 0.1])  # first get at 0.0, second get at 0.1

    throttle = Throttle(
        interval=0.5, _now=lambda: next(now_calls), _sleep=sleeps.append
    )

    def http_get(url: str, timeout: float) -> bytes:
        return b"data"

    parser = ParserHttp(_http_get=http_get, _throttle=throttle, _sleep=_NO_SLEEP)
    parser.get("http://example.com/1", error_prefix="p")
    parser.get("http://example.com/2", error_prefix="p")

    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Throttle fires once per get() call, not once per retry attempt
# ---------------------------------------------------------------------------


def test_throttle_fires_once_per_get_across_multiple_retry_attempts():
    wait_calls = 0

    class CountingThrottle:
        def wait(self) -> None:
            nonlocal wait_calls
            wait_calls += 1

    attempt = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal attempt
        attempt += 1
        if attempt < 3:
            raise OSError("transient")
        return b"ok"

    parser = ParserHttp(
        retries=3,
        _http_get=http_get,
        _throttle=CountingThrottle(),  # type: ignore[arg-type]
        _sleep=_NO_SLEEP,
    )
    parser.get("http://example.com/", error_prefix="p")
    assert attempt == 3
    assert wait_calls == 1


# ---------------------------------------------------------------------------
# Real httpx User-Agent + per-instance header propagation (via respx)
# ---------------------------------------------------------------------------


@respx.mock
def test_real_httpx_sends_default_user_agent_header():
    route = respx.get("http://example.com/jobs").mock(
        return_value=httpx.Response(200, content=b"ok")
    )

    with ParserHttp() as parser:
        parser.get("http://example.com/jobs", error_prefix="p")

    request = route.calls.last.request
    assert request.headers.get("user-agent") == USER_AGENT


@respx.mock
def test_real_httpx_sends_per_instance_custom_header():
    route = respx.get("http://example.com/jobs").mock(
        return_value=httpx.Response(200, content=b"ok")
    )

    with ParserHttp(headers={"X-Custom": "value"}) as parser:
        parser.get("http://example.com/jobs", error_prefix="p")

    request = route.calls.last.request
    assert request.headers.get("x-custom") == "value"
    assert request.headers.get("user-agent") == USER_AGENT


@respx.mock
def test_real_httpx_custom_header_does_not_override_user_agent():
    route = respx.get("http://example.com/jobs").mock(
        return_value=httpx.Response(200, content=b"ok")
    )

    with ParserHttp(headers={"X-Other": "x"}) as parser:
        parser.get("http://example.com/jobs", error_prefix="p")

    request = route.calls.last.request
    assert request.headers.get("user-agent") == USER_AGENT


# ---------------------------------------------------------------------------
# Context manager closes the httpx.Client on __exit__
# ---------------------------------------------------------------------------


def test_context_manager_closes_client_on_exit():
    def http_get(url: str, timeout: float) -> bytes:
        return b"ok"

    parser = ParserHttp(_http_get=http_get)
    with parser:
        pass
    assert parser._client.is_closed


def test_context_manager_returns_self():
    def http_get(url: str, timeout: float) -> bytes:
        return b"ok"

    parser = ParserHttp(_http_get=http_get)
    with parser as p:
        assert p is parser
