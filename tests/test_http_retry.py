from unittest.mock import MagicMock

import pytest

from application_pipeline.http import HttpRetryError, post_with_retries

_NO_SLEEP = lambda _: None  # noqa: E731


def test_returns_response_on_first_success():
    http_post = MagicMock(return_value={"status": "ok"})

    result = post_with_retries(
        "http://host/api", {}, 30.0, 3, http_post, _sleep=_NO_SLEEP
    )

    assert result == {"status": "ok"}
    assert http_post.call_count == 1


def test_passes_url_payload_timeout_to_http_post():
    http_post = MagicMock(return_value={})

    post_with_retries(
        "http://host/api", {"key": "val"}, 15.0, 1, http_post, _sleep=_NO_SLEEP
    )

    http_post.assert_called_once_with("http://host/api", {"key": "val"}, 15.0)


def test_retries_on_failure_and_returns_on_success():
    http_post = MagicMock(side_effect=[OSError("timeout"), {"status": "ok"}])

    result = post_with_retries(
        "http://host/api", {}, 30.0, 2, http_post, _sleep=_NO_SLEEP
    )

    assert result == {"status": "ok"}
    assert http_post.call_count == 2


def test_raises_http_retry_error_after_retries_exhausted():
    http_post = MagicMock(side_effect=OSError("connection refused"))

    with pytest.raises(HttpRetryError):
        post_with_retries("http://host/api", {}, 30.0, 3, http_post, _sleep=_NO_SLEEP)

    assert http_post.call_count == 3


def test_http_retry_error_chains_original_exception():
    cause = OSError("connection refused")
    http_post = MagicMock(side_effect=cause)

    with pytest.raises(HttpRetryError) as exc_info:
        post_with_retries("http://host/api", {}, 30.0, 1, http_post, _sleep=_NO_SLEEP)

    assert exc_info.value.__cause__ is cause


def test_raises_immediately_when_retries_is_zero():
    http_post = MagicMock(return_value={"status": "ok"})

    with pytest.raises(HttpRetryError):
        post_with_retries("http://host/api", {}, 30.0, 0, http_post, _sleep=_NO_SLEEP)

    assert http_post.call_count == 0


def test_error_message_includes_retry_count():
    http_post = MagicMock(side_effect=OSError("refused"))

    with pytest.raises(HttpRetryError, match="2 retries"):
        post_with_retries("http://host/api", {}, 30.0, 2, http_post, _sleep=_NO_SLEEP)
