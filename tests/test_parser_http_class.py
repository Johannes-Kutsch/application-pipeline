"""Tests for the ParserHttp class interface in parsers/http.py."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from application_pipeline.http import (
    HttpParserFatalError,
    HttpRedirectResponse,
    HttpStubNotRetryableError,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.errors import ParserError
from application_pipeline.parsers.http import (
    REQUEST_PACING,
    USER_AGENT,
    ParserHttp,
    _Throttle,
)

_NO_SLEEP = lambda _: None  # noqa: E731


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_get_returns_bytes_on_success(run_log: RunLog):
    def http_get(url: str, timeout: float) -> bytes:
        return b"hello"

    parser = ParserHttp(run_log=run_log, _http_get=http_get, _sleep=_NO_SLEEP)
    assert parser.get("http://example.com/", error_prefix="test") == b"hello"


# ---------------------------------------------------------------------------
# Retry-then-success
# ---------------------------------------------------------------------------


def test_get_retries_then_returns_bytes_on_second_attempt(run_log: RunLog):
    attempt = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise OSError("timeout")
        return b"ok"

    parser = ParserHttp(
        run_log=run_log, retries=2, _http_get=http_get, _sleep=_NO_SLEEP
    )
    result = parser.get("http://example.com/", error_prefix="test")
    assert result == b"ok"
    assert attempt == 2


# ---------------------------------------------------------------------------
# Retry exhaustion → wrapped ParserError
# ---------------------------------------------------------------------------


def test_get_raises_parser_error_after_retry_exhaustion(run_log: RunLog):
    def http_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    parser = ParserHttp(
        run_log=run_log, retries=2, _http_get=http_get, _sleep=_NO_SLEEP
    )
    with pytest.raises(ParserError):
        parser.get("http://example.com/", error_prefix="myparser")


def test_get_parser_error_message_includes_error_prefix(run_log: RunLog):
    def http_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    parser = ParserHttp(
        run_log=run_log, retries=1, _http_get=http_get, _sleep=_NO_SLEEP
    )
    with pytest.raises(ParserError, match="myprefix"):
        parser.get("http://example.com/", error_prefix="myprefix")


def test_get_parser_error_chains_to_underlying_cause(run_log: RunLog):
    cause = OSError("connection refused")

    def http_get(url: str, timeout: float) -> bytes:
        raise cause

    parser = ParserHttp(
        run_log=run_log, retries=1, _http_get=http_get, _sleep=_NO_SLEEP
    )
    with pytest.raises(ParserError) as exc_info:
        parser.get("http://example.com/", error_prefix="p")

    assert exc_info.value.__cause__ is cause


# ---------------------------------------------------------------------------
# Per-stub HTTP errors (404/400/422) → wrapped ParserError
# ---------------------------------------------------------------------------


def test_get_wraps_stub_not_retryable_as_parser_error(run_log: RunLog):
    def http_get(url: str, timeout: float) -> bytes:
        raise HttpStubNotRetryableError("not found: http://example.com/")

    parser = ParserHttp(
        run_log=run_log, retries=3, _http_get=http_get, _sleep=_NO_SLEEP
    )
    with pytest.raises(ParserError, match="myprefix"):
        parser.get("http://example.com/", error_prefix="myprefix")


def test_get_stub_not_retryable_cause_is_original_exception(run_log: RunLog):
    original = HttpStubNotRetryableError("not found: http://example.com/")

    def http_get(url: str, timeout: float) -> bytes:
        raise original

    parser = ParserHttp(
        run_log=run_log, retries=3, _http_get=http_get, _sleep=_NO_SLEEP
    )
    with pytest.raises(ParserError) as exc_info:
        parser.get("http://example.com/", error_prefix="p")

    assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# Parser-fatal HTTP errors (401/403/5xx/unknown) → HttpParserFatalError propagates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["auth:", "upstream:", "unexpected:"])
def test_get_propagates_parser_fatal_error_unwrapped(run_log: RunLog, reason: str):
    def http_get(url: str, timeout: float) -> bytes:
        raise HttpParserFatalError(reason)

    parser = ParserHttp(
        run_log=run_log, retries=3, _http_get=http_get, _sleep=_NO_SLEEP
    )
    with pytest.raises(HttpParserFatalError):
        parser.get("http://example.com/", error_prefix="p")


def test_get_parser_fatal_is_not_wrapped_in_parser_error(run_log: RunLog):
    def http_get(url: str, timeout: float) -> bytes:
        raise HttpParserFatalError("auth: url status=401")

    parser = ParserHttp(
        run_log=run_log, retries=3, _http_get=http_get, _sleep=_NO_SLEEP
    )
    with pytest.raises(Exception) as exc_info:
        parser.get("http://example.com/", error_prefix="p")
    assert not isinstance(exc_info.value, ParserError)


def test_get_parser_fatal_fires_on_first_attempt_without_further_tries(
    run_log: RunLog,
):
    calls = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        raise HttpParserFatalError("auth: url status=401")

    parser = ParserHttp(
        run_log=run_log, retries=3, _http_get=http_get, _sleep=_NO_SLEEP
    )
    with pytest.raises(HttpParserFatalError):
        parser.get("http://example.com/", error_prefix="p")
    assert calls == 1


# ---------------------------------------------------------------------------
# Per-stub and parser-fatal errors both skip the retry loop
# ---------------------------------------------------------------------------


def test_get_stub_not_retryable_fires_on_first_attempt_without_further_tries(
    run_log: RunLog,
):
    calls = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        raise HttpStubNotRetryableError("not found: url")

    parser = ParserHttp(
        run_log=run_log, retries=3, _http_get=http_get, _sleep=_NO_SLEEP
    )
    with pytest.raises(ParserError):
        parser.get("http://example.com/", error_prefix="p")
    assert calls == 1


# ---------------------------------------------------------------------------
# Retryable statuses traverse the retry loop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_get_retries_on_retryable_status_codes(run_log: RunLog, status: int):
    req = httpx.Request("GET", "http://example.com/")
    resp = httpx.Response(status, request=req)
    attempt = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal attempt
        attempt += 1
        if attempt < 2:
            raise httpx.HTTPStatusError(f"{status}", request=req, response=resp)
        return b"ok"

    parser = ParserHttp(
        run_log=run_log, retries=3, _http_get=http_get, _sleep=_NO_SLEEP
    )
    result = parser.get("http://example.com/", error_prefix="p")
    assert result == b"ok"
    assert attempt == 2


# ---------------------------------------------------------------------------
# Throttle pacing across two get() calls
# ---------------------------------------------------------------------------


def test_throttle_pacing_between_consecutive_get_calls(run_log: RunLog):
    sleeps: list[float] = []
    now_calls = iter([0.0, 0.1, 0.1])  # first get at 0.0, second get at 0.1

    throttle = _Throttle(
        interval=0.5, _now=lambda: next(now_calls), _sleep=sleeps.append
    )

    def http_get(url: str, timeout: float) -> bytes:
        return b"data"

    parser = ParserHttp(
        run_log=run_log, _http_get=http_get, _throttle=throttle, _sleep=_NO_SLEEP
    )
    parser.get("http://example.com/1", error_prefix="p")
    parser.get("http://example.com/2", error_prefix="p")

    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.4)


def test_default_throttle_uses_injected_sleep_for_consecutive_mocked_requests(
    run_log: RunLog,
):
    sleeps: list[float] = []

    def http_get(url: str, timeout: float) -> bytes:
        return b"data"

    parser = ParserHttp(
        run_log=run_log,
        _http_get=http_get,
        _sleep=sleeps.append,
    )

    parser.get("http://example.com/1", error_prefix="discover")
    parser.enrich_get("http://example.com/2", error_prefix="enrich")

    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(REQUEST_PACING, abs=0.05)


# ---------------------------------------------------------------------------
# Throttle fires once per get() call, not once per retry attempt
# ---------------------------------------------------------------------------


def test_throttle_fires_once_per_get_across_multiple_retry_attempts(run_log: RunLog):
    # A real Throttle whose `_now` iterator runs dry if `wait()` is called more
    # than once — proves throttle pacing is applied per get(), not per retry.
    now_values = iter([0.0])
    throttle = _Throttle(interval=0.5, _now=lambda: next(now_values), _sleep=_NO_SLEEP)

    attempt = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal attempt
        attempt += 1
        if attempt < 3:
            raise OSError("transient")
        return b"ok"

    parser = ParserHttp(
        run_log=run_log,
        retries=3,
        _http_get=http_get,
        _throttle=throttle,
        _sleep=_NO_SLEEP,
    )
    parser.get("http://example.com/", error_prefix="p")
    assert attempt == 3


# ---------------------------------------------------------------------------
# Real httpx User-Agent + per-instance header propagation (via respx)
# ---------------------------------------------------------------------------


@respx.mock
def test_real_httpx_sends_default_user_agent_header(run_log: RunLog):
    route = respx.get("http://example.com/jobs").mock(
        return_value=httpx.Response(200, content=b"ok")
    )

    with ParserHttp(run_log=run_log) as parser:
        parser.get("http://example.com/jobs", error_prefix="p")

    request = route.calls.last.request
    assert request.headers.get("user-agent") == USER_AGENT


@respx.mock
def test_real_httpx_sends_per_instance_custom_header(run_log: RunLog):
    route = respx.get("http://example.com/jobs").mock(
        return_value=httpx.Response(200, content=b"ok")
    )

    with ParserHttp(run_log=run_log, headers={"X-Custom": "value"}) as parser:
        parser.get("http://example.com/jobs", error_prefix="p")

    request = route.calls.last.request
    assert request.headers.get("x-custom") == "value"
    assert request.headers.get("user-agent") == USER_AGENT


@respx.mock
def test_real_httpx_custom_header_does_not_override_user_agent(run_log: RunLog):
    route = respx.get("http://example.com/jobs").mock(
        return_value=httpx.Response(200, content=b"ok")
    )

    with ParserHttp(run_log=run_log, headers={"X-Other": "x"}) as parser:
        parser.get("http://example.com/jobs", error_prefix="p")

    request = route.calls.last.request
    assert request.headers.get("user-agent") == USER_AGENT


# ---------------------------------------------------------------------------
# Context manager closes the httpx.Client on __exit__
# ---------------------------------------------------------------------------


def test_context_manager_closes_client_on_exit(run_log: RunLog):
    def http_get(url: str, timeout: float) -> bytes:
        return b"ok"

    parser = ParserHttp(run_log=run_log, _http_get=http_get)
    with parser:
        pass
    assert parser._client.is_closed


def test_context_manager_returns_self(run_log: RunLog):
    def http_get(url: str, timeout: float) -> bytes:
        return b"ok"

    parser = ParserHttp(run_log=run_log, _http_get=http_get)
    with parser as p:
        assert p is parser


# ---------------------------------------------------------------------------
# Structured event emission — http_get_skipped / http_get_fatal
# ---------------------------------------------------------------------------


def _read_events(run_log: RunLog, component_id: str) -> list[dict[str, object]]:
    path = run_log._component_path(component_id, "events.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()]


@respx.mock
def test_get_emits_http_get_skipped_event_on_404(run_log: RunLog):
    respx.get("http://example.com/job/1").mock(return_value=httpx.Response(404))

    with ParserHttp(run_log=run_log) as parser:
        with pytest.raises(ParserError):
            parser.get("http://example.com/job/1", error_prefix="p")

    skipped = [
        e
        for e in _read_events(run_log, "parser_http")
        if e["event"] == "http_get_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0]["url"] == "http://example.com/job/1"
    assert skipped[0]["status"] == 404


@respx.mock
def test_get_emits_http_get_fatal_event_on_401(run_log: RunLog):
    respx.get("http://example.com/job/1").mock(return_value=httpx.Response(401))

    with ParserHttp(run_log=run_log) as parser:
        with pytest.raises(HttpParserFatalError):
            parser.get("http://example.com/job/1", error_prefix="p")

    fatal = [
        e
        for e in _read_events(run_log, "parser_http")
        if e["event"] == "http_get_fatal"
    ]
    assert len(fatal) == 1
    assert fatal[0]["url"] == "http://example.com/job/1"
    assert fatal[0]["status"] == 401


@pytest.mark.parametrize("status", [400, 422])
@respx.mock
def test_get_wraps_400_and_422_as_parser_error(run_log: RunLog, status: int):
    respx.get("http://example.com/job/1").mock(return_value=httpx.Response(status))

    with ParserHttp(run_log=run_log) as parser:
        with pytest.raises(ParserError):
            parser.get("http://example.com/job/1", error_prefix="p")


@respx.mock
def test_get_raises_parser_fatal_for_non_retryable_5xx(run_log: RunLog):
    respx.get("http://example.com/job/1").mock(return_value=httpx.Response(501))

    with ParserHttp(run_log=run_log) as parser:
        with pytest.raises(HttpParserFatalError):
            parser.get("http://example.com/job/1", error_prefix="p")


@respx.mock
def test_get_raises_parser_fatal_for_unknown_status(run_log: RunLog):
    respx.get("http://example.com/job/1").mock(return_value=httpx.Response(451))

    with ParserHttp(run_log=run_log) as parser:
        with pytest.raises(HttpParserFatalError):
            parser.get("http://example.com/job/1", error_prefix="p")


# ---------------------------------------------------------------------------
# 3xx surfaces as HttpRedirectResponse (ADR-0037)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@respx.mock
def test_get_raises_redirect_response_on_3xx(run_log: RunLog, status: int):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(
            status, headers={"Location": "http://other.example/landing"}
        )
    )

    with ParserHttp(run_log=run_log) as parser:
        with pytest.raises(HttpRedirectResponse) as exc_info:
            parser.get("http://example.com/job/1", error_prefix="p")

    assert exc_info.value.status == status
    assert exc_info.value.location == "http://other.example/landing"


@respx.mock
def test_get_redirect_is_not_wrapped_in_parser_error(run_log: RunLog):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(302, headers={"Location": "http://elsewhere/"})
    )

    with ParserHttp(run_log=run_log) as parser:
        with pytest.raises(Exception) as exc_info:
            parser.get("http://example.com/job/1", error_prefix="p")
    assert isinstance(exc_info.value, HttpRedirectResponse)
    assert not isinstance(exc_info.value, ParserError)


@respx.mock
def test_get_emits_http_get_redirect_event_on_3xx(run_log: RunLog):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(302, headers={"Location": "http://elsewhere/x"})
    )

    with ParserHttp(run_log=run_log) as parser:
        with pytest.raises(HttpRedirectResponse):
            parser.get("http://example.com/job/1", error_prefix="p")

    events = _read_events(run_log, "parser_http")
    redirects = [e for e in events if e["event"] == "http_get_redirect"]
    assert len(redirects) == 1
    assert redirects[0]["url"] == "http://example.com/job/1"
    assert redirects[0]["status"] == 302
    assert redirects[0]["location"] == "http://elsewhere/x"
    assert not any(e["event"] == "http_get_fatal" for e in events)


@respx.mock
def test_get_redirect_with_missing_location_header_carries_empty_string(
    run_log: RunLog,
):
    respx.get("http://example.com/job/1").mock(return_value=httpx.Response(302))

    with ParserHttp(run_log=run_log) as parser:
        with pytest.raises(HttpRedirectResponse) as exc_info:
            parser.get("http://example.com/job/1", error_prefix="p")

    assert exc_info.value.location == ""
    redirects = [
        e
        for e in _read_events(run_log, "parser_http")
        if e["event"] == "http_get_redirect"
    ]
    assert redirects[0]["location"] == ""


@respx.mock
def test_get_redirect_fires_on_first_attempt_without_retry(run_log: RunLog):
    route = respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(302, headers={"Location": "http://x/"})
    )

    with ParserHttp(run_log=run_log, retries=3, _sleep=_NO_SLEEP) as parser:
        with pytest.raises(HttpRedirectResponse):
            parser.get("http://example.com/job/1", error_prefix="p")
    assert route.call_count == 1


@respx.mock
def test_real_httpx_does_not_follow_redirects(run_log: RunLog):
    redirect_route = respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(302, headers={"Location": "http://example.com/x"})
    )
    target_route = respx.get("http://example.com/x").mock(
        return_value=httpx.Response(200, content=b"target")
    )

    with ParserHttp(run_log=run_log) as parser:
        with pytest.raises(HttpRedirectResponse):
            parser.get("http://example.com/job/1", error_prefix="p")

    assert redirect_route.call_count == 1
    assert target_route.call_count == 0
