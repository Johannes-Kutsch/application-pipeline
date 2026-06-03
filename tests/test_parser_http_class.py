"""Tests for the ParserHttp class interface in parsers/http.py."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from application_pipeline.http import (
    HttpParserFatalError,
    HttpRedirectResponse,
    HttpStubNotRetryableError,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.errors import ParserError
from application_pipeline.parsers.http import (
    HTTP_READ_TIMEOUT,
    REQUEST_PACING,
    ParserHttp,
    ScriptedParserHttpRequest,
    ScriptedParserHttpResponse,
    ScriptedParserHttpTransport,
    _Throttle,
)
from application_pipeline.parsers.types import EnrichFailedError

_NO_SLEEP = lambda _: None  # noqa: E731


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


class _CloseTrackingTransport:
    def __init__(
        self, *outcomes: bytes | Exception | ScriptedParserHttpResponse
    ) -> None:
        self._transport = ScriptedParserHttpTransport(list(outcomes))
        self.closed = False
        self.close_calls = 0

    @property
    def requests(self) -> list[ScriptedParserHttpRequest]:
        return self._transport.requests

    def get(self, url: str, *, timeout: float) -> httpx.Response:
        return self._transport.get(url, timeout=timeout)

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True
        self._transport.close()


def _make_scripted_parser(
    run_log: RunLog,
    *outcomes: bytes | Exception | ScriptedParserHttpResponse,
    retries: int = 3,
    timeout: float = HTTP_READ_TIMEOUT,
    sleep=_NO_SLEEP,
    throttle: _Throttle | None = None,
) -> tuple[ParserHttp, ScriptedParserHttpTransport]:
    transport = ScriptedParserHttpTransport(list(outcomes))
    parser = ParserHttp(
        run_log=run_log,
        retries=retries,
        timeout=timeout,
        _transport=transport,
        _throttle=throttle,
        _sleep=sleep,
    )
    return parser, transport


# ---------------------------------------------------------------------------
# Scripted transport facts stay observable through ParserHttp
# ---------------------------------------------------------------------------


def test_parser_http_replays_scripted_outcomes_and_records_requests(
    run_log: RunLog,
):
    parser, transport = _make_scripted_parser(
        run_log,
        b"first",
        OSError("transient"),
        b"second",
        ScriptedParserHttpResponse.redirect(
            status=302,
            location="http://other.example/landing",
        ),
    )

    assert parser.get("http://example.com/1", error_prefix="discover") == b"first"
    assert parser.enrich_get("http://example.com/2", error_prefix="enrich") == b"second"
    with pytest.raises(HttpRedirectResponse, match="302"):
        parser.get("http://example.com/3", error_prefix="discover")

    assert transport.requests == [
        ScriptedParserHttpRequest(
            url="http://example.com/1",
            timeout=HTTP_READ_TIMEOUT,
        ),
        ScriptedParserHttpRequest(
            url="http://example.com/2",
            timeout=HTTP_READ_TIMEOUT,
        ),
        ScriptedParserHttpRequest(
            url="http://example.com/2",
            timeout=HTTP_READ_TIMEOUT,
        ),
        ScriptedParserHttpRequest(
            url="http://example.com/3",
            timeout=HTTP_READ_TIMEOUT,
        ),
    ]


def test_parser_http_wraps_exhausted_scripted_outcomes(run_log: RunLog):
    parser, transport = _make_scripted_parser(run_log, b"only", retries=1)
    assert parser.get("http://example.com/1", error_prefix="discover") == b"only"

    with pytest.raises(ParserError, match="discover") as exc_info:
        parser.get("http://example.com/2", error_prefix="discover")

    assert isinstance(exc_info.value.__cause__, AssertionError)
    assert "ran out of outcomes" in str(exc_info.value.__cause__)
    assert transport.requests == [
        ScriptedParserHttpRequest(
            url="http://example.com/1", timeout=HTTP_READ_TIMEOUT
        ),
        ScriptedParserHttpRequest(
            url="http://example.com/2", timeout=HTTP_READ_TIMEOUT
        ),
    ]


def test_parser_http_wraps_requests_after_scripted_transport_close(run_log: RunLog):
    parser, transport = _make_scripted_parser(run_log, b"unused", retries=1)
    parser.close()

    with pytest.raises(ParserError, match="discover") as exc_info:
        parser.get("http://example.com/", error_prefix="discover")

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "client has been closed" in str(exc_info.value.__cause__)
    assert transport.requests == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_get_returns_bytes_on_success(run_log: RunLog):
    parser, transport = _make_scripted_parser(run_log, b"hello")
    assert parser.get("http://example.com/", error_prefix="test") == b"hello"
    assert transport.requests == [
        ScriptedParserHttpRequest(
            url="http://example.com/",
            timeout=HTTP_READ_TIMEOUT,
        )
    ]


def test_get_passes_configured_timeout_to_scripted_transport(run_log: RunLog):
    parser, transport = _make_scripted_parser(run_log, b"hello", timeout=7.5)

    assert parser.get("http://example.com/", error_prefix="test") == b"hello"
    assert transport.requests == [
        ScriptedParserHttpRequest(url="http://example.com/", timeout=7.5)
    ]


def test_enrich_get_returns_bytes_on_success(run_log: RunLog):
    parser, transport = _make_scripted_parser(run_log, b"hello")

    assert parser.enrich_get("http://example.com/", error_prefix="test") == b"hello"
    assert transport.requests == [
        ScriptedParserHttpRequest(
            url="http://example.com/",
            timeout=HTTP_READ_TIMEOUT,
        )
    ]


# ---------------------------------------------------------------------------
# Retry-then-success
# ---------------------------------------------------------------------------


def test_get_retries_then_returns_bytes_on_second_attempt(run_log: RunLog):
    parser, transport = _make_scripted_parser(
        run_log,
        OSError("timeout"),
        b"ok",
        retries=2,
    )
    result = parser.get("http://example.com/", error_prefix="test")
    assert result == b"ok"
    assert len(transport.requests) == 2


def test_enrich_get_retries_then_returns_bytes_on_second_attempt(run_log: RunLog):
    parser, transport = _make_scripted_parser(
        run_log,
        OSError("timeout"),
        b"ok",
        retries=2,
    )
    result = parser.enrich_get("http://example.com/", error_prefix="test")
    assert result == b"ok"
    assert len(transport.requests) == 2


# ---------------------------------------------------------------------------
# Retry exhaustion → wrapped ParserError
# ---------------------------------------------------------------------------


def test_get_raises_parser_error_after_retry_exhaustion(run_log: RunLog):
    parser, _ = _make_scripted_parser(
        run_log, OSError("refused"), OSError("refused"), retries=2
    )
    with pytest.raises(ParserError):
        parser.get("http://example.com/", error_prefix="myparser")


def test_get_parser_error_message_includes_error_prefix(run_log: RunLog):
    parser, _ = _make_scripted_parser(run_log, OSError("refused"), retries=1)
    with pytest.raises(ParserError, match="myprefix"):
        parser.get("http://example.com/", error_prefix="myprefix")


def test_get_parser_error_chains_to_underlying_cause(run_log: RunLog):
    cause = OSError("connection refused")
    parser, _ = _make_scripted_parser(run_log, cause, retries=1)
    with pytest.raises(ParserError) as exc_info:
        parser.get("http://example.com/", error_prefix="p")

    assert exc_info.value.__cause__ is cause


# ---------------------------------------------------------------------------
# Per-stub HTTP errors (404/400/422) → wrapped ParserError
# ---------------------------------------------------------------------------


def test_get_wraps_stub_not_retryable_as_parser_error(run_log: RunLog):
    parser, _ = _make_scripted_parser(
        run_log,
        ScriptedParserHttpResponse(status=404),
    )
    with pytest.raises(ParserError, match="myprefix"):
        parser.get("http://example.com/", error_prefix="myprefix")


def test_get_stub_not_retryable_cause_is_original_exception(run_log: RunLog):
    original = HttpStubNotRetryableError("not found: http://example.com/")
    parser, _ = _make_scripted_parser(run_log, original)
    with pytest.raises(ParserError) as exc_info:
        parser.get("http://example.com/", error_prefix="p")

    assert exc_info.value.__cause__ is original


def test_enrich_get_wraps_stub_not_retryable_as_enrich_failed_error(run_log: RunLog):
    parser, _ = _make_scripted_parser(
        run_log,
        ScriptedParserHttpResponse(status=404),
    )
    with pytest.raises(EnrichFailedError, match="myprefix"):
        parser.enrich_get("http://example.com/", error_prefix="myprefix")


# ---------------------------------------------------------------------------
# Parser-fatal HTTP errors (401/403/5xx/unknown) → HttpParserFatalError propagates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["auth:", "upstream:", "unexpected:"])
def test_get_propagates_parser_fatal_error_unwrapped(run_log: RunLog, reason: str):
    parser, _ = _make_scripted_parser(run_log, HttpParserFatalError(reason))
    with pytest.raises(HttpParserFatalError):
        parser.get("http://example.com/", error_prefix="p")


def test_get_parser_fatal_is_not_wrapped_in_parser_error(run_log: RunLog):
    parser, _ = _make_scripted_parser(run_log, ScriptedParserHttpResponse(status=401))
    with pytest.raises(Exception) as exc_info:
        parser.get("http://example.com/", error_prefix="p")
    assert not isinstance(exc_info.value, ParserError)


def test_enrich_get_parser_fatal_is_not_wrapped_in_enrich_failed_error(
    run_log: RunLog,
):
    parser, _ = _make_scripted_parser(run_log, ScriptedParserHttpResponse(status=401))
    with pytest.raises(Exception) as exc_info:
        parser.enrich_get("http://example.com/", error_prefix="p")
    assert isinstance(exc_info.value, HttpParserFatalError)
    assert not isinstance(exc_info.value, EnrichFailedError)


def test_get_parser_fatal_fires_on_first_attempt_without_further_tries(
    run_log: RunLog,
):
    parser, transport = _make_scripted_parser(
        run_log, ScriptedParserHttpResponse(status=401)
    )
    with pytest.raises(HttpParserFatalError):
        parser.get("http://example.com/", error_prefix="p")
    assert len(transport.requests) == 1


# ---------------------------------------------------------------------------
# Per-stub and parser-fatal errors both skip the retry loop
# ---------------------------------------------------------------------------


def test_get_stub_not_retryable_fires_on_first_attempt_without_further_tries(
    run_log: RunLog,
):
    parser, transport = _make_scripted_parser(
        run_log, ScriptedParserHttpResponse(status=404)
    )
    with pytest.raises(ParserError):
        parser.get("http://example.com/", error_prefix="p")
    assert len(transport.requests) == 1


# ---------------------------------------------------------------------------
# Retryable statuses traverse the retry loop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_get_retries_on_retryable_status_codes(run_log: RunLog, status: int):
    parser, transport = _make_scripted_parser(
        run_log,
        httpx.HTTPStatusError(
            f"{status}",
            request=httpx.Request("GET", "http://example.com/"),
            response=httpx.Response(status),
        ),
        b"ok",
    )
    result = parser.get("http://example.com/", error_prefix="p")
    assert result == b"ok"
    assert len(transport.requests) == 2


# ---------------------------------------------------------------------------
# Throttle pacing across two get() calls
# ---------------------------------------------------------------------------


def test_throttle_pacing_between_consecutive_get_calls(run_log: RunLog):
    sleeps: list[float] = []
    now_calls = iter([0.0, 0.1, 0.1])  # first get at 0.0, second get at 0.1

    throttle = _Throttle(
        interval=0.5, _now=lambda: next(now_calls), _sleep=sleeps.append
    )

    parser, _ = _make_scripted_parser(
        run_log,
        b"data",
        b"data",
        throttle=throttle,
    )
    parser.get("http://example.com/1", error_prefix="p")
    parser.get("http://example.com/2", error_prefix="p")

    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.4)


def test_default_throttle_uses_injected_sleep_for_consecutive_mocked_requests(
    run_log: RunLog,
):
    sleeps: list[float] = []
    parser, _ = _make_scripted_parser(
        run_log,
        b"data",
        b"data",
        sleep=sleeps.append,
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

    parser, transport = _make_scripted_parser(
        run_log,
        OSError("transient"),
        OSError("transient"),
        b"ok",
        retries=3,
        throttle=throttle,
    )
    parser.get("http://example.com/", error_prefix="p")
    assert len(transport.requests) == 3


# ---------------------------------------------------------------------------
# Explicit close/context boundary owns the transport lifetime
# ---------------------------------------------------------------------------


def test_close_prevents_further_http_requests(run_log: RunLog):
    transport = _CloseTrackingTransport(b"ok")
    parser = ParserHttp(
        run_log=run_log,
        retries=1,
        _transport=transport,
        _sleep=_NO_SLEEP,
    )
    parser.close()

    with pytest.raises(ParserError, match="p") as exc_info:
        parser.get("http://example.com/jobs", error_prefix="p")

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert transport.closed is True
    assert transport.close_calls == 1
    assert transport.requests == []


def test_context_manager_closes_transport_on_exit(run_log: RunLog):
    transport = _CloseTrackingTransport(b"ok")
    parser = ParserHttp(
        run_log=run_log,
        retries=1,
        _transport=transport,
        _sleep=_NO_SLEEP,
    )
    with parser:
        pass

    with pytest.raises(ParserError, match="p") as exc_info:
        parser.get("http://example.com/jobs", error_prefix="p")

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert transport.closed is True
    assert transport.close_calls == 1
    assert transport.requests == []


def test_context_manager_returns_self(run_log: RunLog):
    parser, _ = _make_scripted_parser(run_log, b"ok")
    with parser as p:
        assert p is parser


def test_context_manager_closes_custom_transport_adapter_without_httpx_client(
    run_log: RunLog,
):
    transport = _CloseTrackingTransport(b"ok")
    parser = ParserHttp(run_log=run_log, _transport=transport, _sleep=_NO_SLEEP)

    with parser:
        assert parser.get("http://example.com/jobs", error_prefix="p") == b"ok"

    assert transport.closed is True
    assert transport.requests == [
        ScriptedParserHttpRequest(
            url="http://example.com/jobs",
            timeout=HTTP_READ_TIMEOUT,
        )
    ]


# ---------------------------------------------------------------------------
# Structured event emission — http_get_skipped / http_get_fatal
# ---------------------------------------------------------------------------


def _read_events(run_log: RunLog, component_id: str) -> list[dict[str, object]]:
    if component_id.startswith("parser_"):
        path = (
            run_log.logs_dir
            / "parser"
            / f"{component_id.removeprefix('parser_')}.events.jsonl"
        )
    elif component_id.startswith("llm_"):
        path = (
            run_log.logs_dir
            / "llm"
            / f"{component_id.removeprefix('llm_')}.events.jsonl"
        )
    elif component_id.startswith("pipeline_"):
        path = (
            run_log.logs_dir
            / "pipeline"
            / f"{component_id.removeprefix('pipeline_')}.events.jsonl"
        )
    else:
        path = run_log.logs_dir / f"{component_id}.events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_get_emits_http_get_skipped_event_on_404(run_log: RunLog):
    parser, _ = _make_scripted_parser(run_log, ScriptedParserHttpResponse(status=404))
    with parser:
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


def test_get_emits_http_get_fatal_event_on_401(run_log: RunLog):
    parser, _ = _make_scripted_parser(run_log, ScriptedParserHttpResponse(status=401))
    with parser:
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
def test_get_wraps_400_and_422_as_parser_error(run_log: RunLog, status: int):
    parser, _ = _make_scripted_parser(
        run_log,
        ScriptedParserHttpResponse(status=status),
    )
    with parser:
        with pytest.raises(ParserError):
            parser.get("http://example.com/job/1", error_prefix="p")


def test_get_raises_parser_fatal_for_non_retryable_5xx(run_log: RunLog):
    parser, _ = _make_scripted_parser(run_log, ScriptedParserHttpResponse(status=501))
    with parser:
        with pytest.raises(HttpParserFatalError):
            parser.get("http://example.com/job/1", error_prefix="p")


def test_get_raises_parser_fatal_for_unknown_status(run_log: RunLog):
    parser, _ = _make_scripted_parser(run_log, ScriptedParserHttpResponse(status=451))
    with parser:
        with pytest.raises(HttpParserFatalError):
            parser.get("http://example.com/job/1", error_prefix="p")


# ---------------------------------------------------------------------------
# 3xx surfaces as HttpRedirectResponse (ADR-0037)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_get_raises_redirect_response_on_3xx(run_log: RunLog, status: int):
    parser, _ = _make_scripted_parser(
        run_log,
        ScriptedParserHttpResponse.redirect(
            status=status,
            location="http://other.example/landing",
        ),
    )
    with parser:
        with pytest.raises(HttpRedirectResponse) as exc_info:
            parser.get("http://example.com/job/1", error_prefix="p")

    assert exc_info.value.status == status
    assert exc_info.value.location == "http://other.example/landing"


def test_get_redirect_is_not_wrapped_in_parser_error(run_log: RunLog):
    parser, _ = _make_scripted_parser(
        run_log,
        ScriptedParserHttpResponse.redirect(
            status=302,
            location="http://elsewhere/",
        ),
    )
    with parser:
        with pytest.raises(Exception) as exc_info:
            parser.get("http://example.com/job/1", error_prefix="p")
    assert isinstance(exc_info.value, HttpRedirectResponse)
    assert not isinstance(exc_info.value, ParserError)


def test_get_emits_http_get_redirect_event_on_3xx(run_log: RunLog):
    parser, _ = _make_scripted_parser(
        run_log,
        ScriptedParserHttpResponse.redirect(
            status=302,
            location="http://elsewhere/x",
        ),
    )
    with parser:
        with pytest.raises(HttpRedirectResponse):
            parser.get("http://example.com/job/1", error_prefix="p")

    events = _read_events(run_log, "parser_http")
    redirects = [e for e in events if e["event"] == "http_get_redirect"]
    assert len(redirects) == 1
    assert redirects[0]["url"] == "http://example.com/job/1"
    assert redirects[0]["status"] == 302
    assert redirects[0]["location"] == "http://elsewhere/x"
    assert not any(e["event"] == "http_get_fatal" for e in events)


def test_get_redirect_with_missing_location_header_carries_empty_string(
    run_log: RunLog,
):
    parser, _ = _make_scripted_parser(run_log, ScriptedParserHttpResponse(status=302))
    with parser:
        with pytest.raises(HttpRedirectResponse) as exc_info:
            parser.get("http://example.com/job/1", error_prefix="p")

    assert exc_info.value.location == ""
    redirects = [
        e
        for e in _read_events(run_log, "parser_http")
        if e["event"] == "http_get_redirect"
    ]
    assert redirects[0]["location"] == ""


def test_get_redirect_fires_on_first_attempt_without_retry(run_log: RunLog):
    parser, transport = _make_scripted_parser(
        run_log,
        ScriptedParserHttpResponse.redirect(
            status=302,
            location="http://x/",
        ),
        retries=3,
    )
    with parser:
        with pytest.raises(HttpRedirectResponse):
            parser.get("http://example.com/job/1", error_prefix="p")
    assert len(transport.requests) == 1


def test_enrich_get_raises_redirect_response_on_3xx(run_log: RunLog):
    parser, _ = _make_scripted_parser(
        run_log,
        ScriptedParserHttpResponse.redirect(
            status=302,
            location="http://example.com/x",
        ),
    )

    with pytest.raises(HttpRedirectResponse) as exc_info:
        parser.enrich_get("http://example.com/job/1", error_prefix="p")

    assert exc_info.value.status == 302
    assert exc_info.value.location == "http://example.com/x"
