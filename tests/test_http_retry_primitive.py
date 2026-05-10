"""Tests for the shared retry primitive in http/retry.py."""

from __future__ import annotations

import pytest

from application_pipeline.http.retry import (
    HttpNotRetryableError,
    HttpRetryError,
    exponential_backoff,
    retry,
)


# ---------------------------------------------------------------------------
# exponential_backoff
# ---------------------------------------------------------------------------


def test_backoff_first_attempt_equals_initial():
    policy = exponential_backoff(1.0, 2.0, 8.0)
    assert policy(0) == pytest.approx(1.0)


def test_backoff_second_attempt_doubles():
    policy = exponential_backoff(1.0, 2.0, 8.0)
    assert policy(1) == pytest.approx(2.0)


def test_backoff_third_attempt():
    policy = exponential_backoff(1.0, 2.0, 8.0)
    assert policy(2) == pytest.approx(4.0)


def test_backoff_caps_at_max():
    policy = exponential_backoff(1.0, 2.0, 8.0)
    assert policy(3) == pytest.approx(8.0)
    assert policy(10) == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# retry — success paths
# ---------------------------------------------------------------------------


def test_retry_returns_result_on_first_success():
    result = retry(
        lambda: 42,
        predicate=lambda exc: True,
        backoff_policy=exponential_backoff(1.0, 2.0, 8.0),
        max_retries=3,
        error_factory=lambda n, exc: HttpRetryError(f"failed after {n}"),
        _sleep=lambda _: None,
    )
    assert result == 42


def test_retry_returns_result_after_one_failure():
    calls = [0]

    def fn() -> int:
        calls[0] += 1
        if calls[0] == 1:
            raise OSError("transient")
        return 99

    result = retry(
        fn,
        predicate=lambda exc: True,
        backoff_policy=exponential_backoff(1.0, 2.0, 8.0),
        max_retries=3,
        error_factory=lambda n, exc: HttpRetryError(f"failed after {n}"),
        _sleep=lambda _: None,
    )
    assert result == 99
    assert calls[0] == 2


# ---------------------------------------------------------------------------
# retry — exhaustion
# ---------------------------------------------------------------------------


def test_retry_raises_error_factory_result_after_exhausting_retries():
    with pytest.raises(HttpRetryError, match="failed after 2"):
        retry(
            lambda: (_ for _ in ()).throw(OSError("always fails")),
            predicate=lambda exc: True,
            backoff_policy=exponential_backoff(1.0, 2.0, 8.0),
            max_retries=2,
            error_factory=lambda n, exc: HttpRetryError(f"failed after {n}"),
            _sleep=lambda _: None,
        )


def test_retry_chains_last_exception():
    cause = OSError("root cause")

    with pytest.raises(HttpRetryError) as exc_info:
        retry(
            lambda: (_ for _ in ()).throw(cause),
            predicate=lambda exc: True,
            backoff_policy=exponential_backoff(1.0, 2.0, 8.0),
            max_retries=1,
            error_factory=lambda n, exc: HttpRetryError("exhausted"),
            _sleep=lambda _: None,
        )

    assert exc_info.value.__cause__ is cause


def test_retry_raises_immediately_with_zero_retries():
    called = [False]

    def fn() -> int:
        called[0] = True
        return 1

    with pytest.raises(HttpRetryError):
        retry(
            fn,
            predicate=lambda exc: True,
            backoff_policy=exponential_backoff(1.0, 2.0, 8.0),
            max_retries=0,
            error_factory=lambda n, exc: HttpRetryError("zero retries"),
            _sleep=lambda _: None,
        )

    assert not called[0]


# ---------------------------------------------------------------------------
# retry — predicate: stops retry when predicate returns False
# ---------------------------------------------------------------------------


def test_retry_reraises_immediately_when_predicate_false():
    class NonRetryable(Exception):
        pass

    with pytest.raises(NonRetryable):
        retry(
            lambda: (_ for _ in ()).throw(NonRetryable("stop")),
            predicate=lambda exc: not isinstance(exc, NonRetryable),
            backoff_policy=exponential_backoff(1.0, 2.0, 8.0),
            max_retries=3,
            error_factory=lambda n, exc: HttpRetryError("should not reach"),
            _sleep=lambda _: None,
        )


def test_retry_does_not_call_fn_again_after_non_retryable():
    calls = [0]

    def fn() -> int:
        calls[0] += 1
        raise HttpNotRetryableError("stop")

    with pytest.raises(HttpNotRetryableError):
        retry(
            fn,
            predicate=lambda exc: not isinstance(exc, HttpNotRetryableError),
            backoff_policy=exponential_backoff(1.0, 2.0, 8.0),
            max_retries=3,
            error_factory=lambda n, exc: HttpRetryError("should not reach"),
            _sleep=lambda _: None,
        )

    assert calls[0] == 1


# ---------------------------------------------------------------------------
# retry — backoff is applied between retries
# ---------------------------------------------------------------------------


def test_retry_calls_sleep_between_retries():
    sleeps: list[float] = []
    calls = [0]

    def fn() -> int:
        calls[0] += 1
        if calls[0] < 3:
            raise OSError("not yet")
        return 0

    retry(
        fn,
        predicate=lambda exc: True,
        backoff_policy=exponential_backoff(1.0, 2.0, 8.0),
        max_retries=3,
        error_factory=lambda n, exc: HttpRetryError("failed"),
        _sleep=sleeps.append,
    )

    assert sleeps == pytest.approx([1.0, 2.0])


def test_retry_does_not_sleep_after_last_attempt():
    sleeps: list[float] = []

    with pytest.raises(HttpRetryError):
        retry(
            lambda: (_ for _ in ()).throw(OSError("fail")),
            predicate=lambda exc: True,
            backoff_policy=exponential_backoff(1.0, 2.0, 8.0),
            max_retries=2,
            error_factory=lambda n, exc: HttpRetryError("failed"),
            _sleep=sleeps.append,
        )

    # Sleep after attempt 0, but NOT after final attempt 1
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(1.0)
