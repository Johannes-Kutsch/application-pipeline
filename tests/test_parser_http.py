from __future__ import annotations

import pytest

import application_pipeline.parser_log as parser_log
from application_pipeline._context import current_stage
from application_pipeline.http import HttpRetryError
from application_pipeline.parsers.http import (
    Throttle,
    request_with_retry,
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


# --- Throttle ---


def test_throttle_does_not_sleep_on_first_call():
    sleeps: list[float] = []
    throttle = Throttle(_sleep=sleeps.append)
    throttle.wait()
    assert sleeps == []


def test_throttle_sleeps_remaining_time_when_called_too_quickly():
    now_calls = iter([0.0, 0.3])
    sleeps: list[float] = []
    throttle = Throttle(
        interval=0.5, _now=lambda: next(now_calls), _sleep=sleeps.append
    )
    throttle.wait()  # first call — records _last = 0.0
    throttle.wait()  # second call at t=0.3, only 0.3s elapsed → sleep 0.2s
    assert sleeps == pytest.approx([0.2])


def test_throttle_does_not_sleep_after_sufficient_delay():
    now_calls = iter([0.0, 0.6])
    sleeps: list[float] = []
    throttle = Throttle(
        interval=0.5, _now=lambda: next(now_calls), _sleep=sleeps.append
    )
    throttle.wait()  # first call — no sleep
    throttle.wait()  # second call at t=0.6 — 0.6 ≥ 0.5, no sleep
    assert sleeps == []


# --- request_with_retry ---


def test_request_with_retry_returns_bytes_on_success():
    def http_get(url: str, timeout: float) -> bytes:
        return b"ok"

    result = request_with_retry("http://host/path", 30.0, 3, http_get, _sleep=_NO_SLEEP)
    assert result == b"ok"


def test_request_with_retry_passes_url_and_timeout():
    calls: list[tuple[str, float]] = []

    def http_get(url: str, timeout: float) -> bytes:
        calls.append((url, timeout))
        return b""

    request_with_retry("http://host/path", 15.0, 1, http_get, _sleep=_NO_SLEEP)
    assert calls == [("http://host/path", 15.0)]


def test_request_with_retry_retries_on_failure_then_succeeds():
    attempt = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise OSError("timeout")
        return b"ok"

    result = request_with_retry("http://host/path", 30.0, 2, http_get, _sleep=_NO_SLEEP)
    assert result == b"ok"
    assert attempt == 2


def test_request_with_retry_raises_http_retry_error_after_exhausting_retries():
    def http_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with pytest.raises(HttpRetryError):
        request_with_retry("http://host/path", 30.0, 3, http_get, _sleep=_NO_SLEEP)


def test_request_with_retry_chains_original_exception():
    cause = OSError("refused")

    def http_get(url: str, timeout: float) -> bytes:
        raise cause

    with pytest.raises(HttpRetryError) as exc_info:
        request_with_retry("http://host/path", 30.0, 1, http_get, _sleep=_NO_SLEEP)

    assert exc_info.value.__cause__ is cause


def test_request_with_retry_raises_immediately_when_retries_zero():
    calls: list[int] = []

    def http_get(url: str, timeout: float) -> bytes:
        calls.append(1)
        return b""

    with pytest.raises(HttpRetryError):
        request_with_retry("http://host/path", 30.0, 0, http_get, _sleep=_NO_SLEEP)

    assert calls == []


def test_request_with_retry_error_message_includes_retry_count():
    def http_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with pytest.raises(HttpRetryError, match="2 retries"):
        request_with_retry("http://host/path", 30.0, 2, http_get, _sleep=_NO_SLEEP)


# --- HTTP log bracketing ---


def test_success_emits_start_and_ok_in_parser_log(log_dir):
    current_stage.set("parser:my_parser")

    def http_get(url: str, timeout: float) -> bytes:
        return b"hello world"

    request_with_retry("http://example.com/feed", 30.0, 1, http_get, _sleep=_NO_SLEEP)

    log = (log_dir / "my_parser.log").read_text(encoding="utf-8")
    assert log.count("http_get_start") == 1
    assert log.count("http_get_ok") == 1
    assert "url=http://example.com/feed" in log
    assert "bytes=11" in log
    assert "elapsed_ms=" in log
    assert "http_get_retry" not in log


def test_hanging_request_leaves_start_without_ok_in_parser_log(log_dir):
    current_stage.set("parser:my_parser")

    def http_get(url: str, timeout: float) -> bytes:
        raise OSError("timed out")

    with pytest.raises(HttpRetryError):
        request_with_retry(
            "http://example.com/feed", 30.0, 1, http_get, _sleep=_NO_SLEEP
        )

    log = (log_dir / "my_parser.log").read_text(encoding="utf-8")
    assert "http_get_start" in log
    assert "http_get_ok" not in log


def test_retry_emits_start_retry_start_ok_on_second_attempt(log_dir):
    current_stage.set("parser:my_parser")
    attempt = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise OSError("refused")
        return b"ok"

    request_with_retry("http://example.com/feed", 30.0, 2, http_get, _sleep=_NO_SLEEP)

    log = (log_dir / "my_parser.log").read_text(encoding="utf-8")
    assert log.count("http_get_start") == 2
    assert log.count("http_get_ok") == 1
    assert log.count("http_get_retry") == 1
    assert "reason=refused" in log


def test_log_routes_to_parser_pid_stripping_prefix(log_dir):
    current_stage.set("parser:jobs_beim_staat_html")

    def http_get(url: str, timeout: float) -> bytes:
        return b"data"

    request_with_retry("http://example.com/", 30.0, 1, http_get, _sleep=_NO_SLEEP)

    assert (log_dir / "jobs_beim_staat_html.log").exists()
    assert not (log_dir / "parser:jobs_beim_staat_html.log").exists()
