"""Tests for HTTP status discrimination and constants in parsers/_http.py and parsers/http.py."""

from __future__ import annotations

import httpx
import pytest
import respx

from application_pipeline.http.retry import HttpNotRetryableError
from application_pipeline.parsers._http import (
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
from application_pipeline.parsers.http import (
    _default_http_get,
    check_response_status,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_http_read_timeout_is_positive():
    assert HTTP_READ_TIMEOUT > 0


def test_http_connect_timeout_is_five_seconds():
    assert HTTP_CONNECT_TIMEOUT == 5.0


def test_max_retries_is_three():
    assert MAX_RETRIES == 3


def test_backoff_initial_is_one():
    assert BACKOFF_INITIAL == 1.0


def test_backoff_multiplier_is_two():
    assert BACKOFF_MULTIPLIER == 2.0


def test_backoff_max_is_eight():
    assert BACKOFF_MAX == 8.0


def test_retry_statuses_contains_expected_codes():
    assert RETRY_STATUSES == frozenset({429, 502, 503, 504})


def test_request_pacing_is_half_second():
    assert REQUEST_PACING == 0.5


def test_user_agent_is_non_empty_string():
    assert isinstance(USER_AGENT, str) and USER_AGENT


# ---------------------------------------------------------------------------
# check_response_status — success
# ---------------------------------------------------------------------------


def _response(status: int, url: str = "http://example.com/path") -> httpx.Response:
    req = httpx.Request("GET", url)
    return httpx.Response(status, request=req)


def test_check_response_status_passes_for_200():
    check_response_status(_response(200), "http://example.com/")


def test_check_response_status_passes_for_201():
    check_response_status(_response(201), "http://example.com/")


# ---------------------------------------------------------------------------
# check_response_status — 404
# ---------------------------------------------------------------------------


def test_check_response_status_raises_not_retryable_on_404():
    with pytest.raises(HttpNotRetryableError, match="not found:"):
        check_response_status(_response(404), "http://example.com/missing")


def test_check_response_status_404_message_contains_url():
    url = "http://example.com/specific-path"
    with pytest.raises(HttpNotRetryableError, match=url):
        check_response_status(_response(404), url)


# ---------------------------------------------------------------------------
# check_response_status — auth errors
# ---------------------------------------------------------------------------


def test_check_response_status_raises_not_retryable_on_401():
    with pytest.raises(HttpNotRetryableError, match="auth:"):
        check_response_status(_response(401), "http://example.com/secure")


def test_check_response_status_raises_not_retryable_on_403():
    with pytest.raises(HttpNotRetryableError, match="auth:"):
        check_response_status(_response(403), "http://example.com/forbidden")


# ---------------------------------------------------------------------------
# check_response_status — malformed errors
# ---------------------------------------------------------------------------


def test_check_response_status_raises_not_retryable_on_400():
    with pytest.raises(HttpNotRetryableError, match="malformed:"):
        check_response_status(_response(400), "http://example.com/bad-request")


def test_check_response_status_raises_not_retryable_on_422():
    with pytest.raises(HttpNotRetryableError, match="malformed:"):
        check_response_status(_response(422), "http://example.com/unprocessable")


# ---------------------------------------------------------------------------
# check_response_status — upstream server errors (non-retryable 5xx)
# ---------------------------------------------------------------------------


def test_check_response_status_raises_not_retryable_on_500():
    with pytest.raises(HttpNotRetryableError, match="upstream:"):
        check_response_status(_response(500), "http://example.com/crash")


def test_check_response_status_raises_not_retryable_on_503_is_retryable():
    # 503 is in RETRY_STATUSES — must NOT raise HttpNotRetryableError
    with pytest.raises(httpx.HTTPStatusError):
        check_response_status(_response(503), "http://example.com/overload")


# ---------------------------------------------------------------------------
# check_response_status — retryable statuses raise httpx.HTTPStatusError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_check_response_status_raises_http_status_error_for_retryable(status: int):
    with pytest.raises(httpx.HTTPStatusError):
        check_response_status(_response(status), "http://example.com/retry")


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_check_response_status_does_not_raise_not_retryable_for_retryable(status: int):
    with pytest.raises(Exception) as exc_info:
        check_response_status(_response(status), "http://example.com/retry")
    assert not isinstance(exc_info.value, HttpNotRetryableError)


# ---------------------------------------------------------------------------
# _default_http_get — User-Agent header and status handling (via respx)
# ---------------------------------------------------------------------------


@respx.mock
def test_default_http_get_sends_user_agent_header():
    route = respx.get("http://example.com/jobs").mock(
        return_value=httpx.Response(200, content=b"ok")
    )

    _default_http_get("http://example.com/jobs", timeout=5.0)

    request = route.calls.last.request
    assert request.headers.get("user-agent") == USER_AGENT


@respx.mock
def test_default_http_get_returns_response_bytes_on_200():
    respx.get("http://example.com/jobs").mock(
        return_value=httpx.Response(200, content=b"data")
    )

    result = _default_http_get("http://example.com/jobs", timeout=5.0)

    assert result == b"data"


@respx.mock
def test_default_http_get_raises_not_retryable_on_404():
    respx.get("http://example.com/missing").mock(return_value=httpx.Response(404))

    with pytest.raises(HttpNotRetryableError, match="not found:"):
        _default_http_get("http://example.com/missing", timeout=5.0)


@respx.mock
def test_default_http_get_raises_not_retryable_on_401():
    respx.get("http://example.com/secure").mock(return_value=httpx.Response(401))

    with pytest.raises(HttpNotRetryableError, match="auth:"):
        _default_http_get("http://example.com/secure", timeout=5.0)


@respx.mock
def test_default_http_get_raises_httpx_status_error_on_retryable_503():
    respx.get("http://example.com/overload").mock(return_value=httpx.Response(503))

    with pytest.raises(httpx.HTTPStatusError):
        _default_http_get("http://example.com/overload", timeout=5.0)
