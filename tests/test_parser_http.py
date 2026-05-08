import pytest

from application_pipeline.http import HttpRetryError
from application_pipeline.parsers.http import (
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    THROTTLE_INTERVAL,
    Throttle,
    request_with_retry,
)


# --- Constants ---


def test_default_timeout_is_positive():
    assert DEFAULT_TIMEOUT > 0


def test_default_retries_is_positive():
    assert DEFAULT_RETRIES > 0


def test_throttle_interval_is_half_second():
    assert THROTTLE_INTERVAL == 0.5


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

    result = request_with_retry("http://host/path", 30.0, 3, http_get)
    assert result == b"ok"


def test_request_with_retry_passes_url_and_timeout():
    calls: list[tuple[str, float]] = []

    def http_get(url: str, timeout: float) -> bytes:
        calls.append((url, timeout))
        return b""

    request_with_retry("http://host/path", 15.0, 1, http_get)
    assert calls == [("http://host/path", 15.0)]


def test_request_with_retry_retries_on_failure_then_succeeds():
    attempt = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise OSError("timeout")
        return b"ok"

    result = request_with_retry("http://host/path", 30.0, 2, http_get)
    assert result == b"ok"
    assert attempt == 2


def test_request_with_retry_raises_http_retry_error_after_exhausting_retries():
    def http_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with pytest.raises(HttpRetryError):
        request_with_retry("http://host/path", 30.0, 3, http_get)


def test_request_with_retry_chains_original_exception():
    cause = OSError("refused")

    def http_get(url: str, timeout: float) -> bytes:
        raise cause

    with pytest.raises(HttpRetryError) as exc_info:
        request_with_retry("http://host/path", 30.0, 1, http_get)

    assert exc_info.value.__cause__ is cause


def test_request_with_retry_raises_immediately_when_retries_zero():
    calls: list[int] = []

    def http_get(url: str, timeout: float) -> bytes:
        calls.append(1)
        return b""

    with pytest.raises(HttpRetryError):
        request_with_retry("http://host/path", 30.0, 0, http_get)

    assert calls == []


def test_request_with_retry_error_message_includes_retry_count():
    def http_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with pytest.raises(HttpRetryError, match="2 retries"):
        request_with_retry("http://host/path", 30.0, 2, http_get)
