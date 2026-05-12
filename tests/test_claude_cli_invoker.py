import json
from typing import Any

import pytest

from application_pipeline.llm.claude_cli import (
    ClaudeCliError,
    ClaudeCliInvoker,
    ClaudeMalformedEnvelopeError,
    ClaudeResponse,
    ClaudeUsageLimitError,
)


def _runner(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    calls: list[tuple[list[str], str]] | None = None,
):
    def run(args: list[str], stdin: str) -> tuple[int, str, str]:
        if calls is not None:
            calls.append((args, stdin))
        return returncode, stdout, stderr

    return run


def _envelope(
    result: Any = None,
    is_error: bool = False,
    usage: dict[str, int] | None = None,
    total_cost_usd: float = 0.001,
    session_id: str = "sess-abc",
) -> str:
    if usage is None:
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0}
    envelope: dict[str, Any] = {
        "type": "result",
        "subtype": "error_during_run" if is_error else "success",
        "is_error": is_error,
        "result": result if isinstance(result, str) else json.dumps(result),
        "total_cost_usd": total_cost_usd,
        "session_id": session_id,
        "usage": usage,
    }
    return json.dumps(envelope)


def _invoker(runner=None) -> ClaudeCliInvoker:
    return ClaudeCliInvoker(cli_path="claude", _runner=runner)


# --- happy path ---


def test_call_returns_parsed_result():
    payload = [{"id": "1", "in_domain": True}]
    response = _invoker(_runner(stdout=_envelope(result=payload))).call("p", "en")
    assert response.parsed_result == payload


def test_call_returns_raw_response():
    payload = {"tier": "green"}
    response = _invoker(_runner(stdout=_envelope(result=payload))).call("p", "en")
    assert json.loads(response.raw_response) == payload


def test_call_returns_usage_input_and_output_tokens():
    usage = {"input_tokens": 300, "output_tokens": 120, "cache_read_input_tokens": 0}
    response = _invoker(
        _runner(stdout=_envelope(result={"ok": True}, usage=usage))
    ).call("p", "de")
    assert response.usage.input_tokens == 300
    assert response.usage.output_tokens == 120


def test_call_returns_cost_usd():
    response = _invoker(
        _runner(stdout=_envelope(result={"ok": True}, total_cost_usd=0.042))
    ).call("p", "en")
    assert response.cost_usd == pytest.approx(0.042)


def test_call_returns_session_id():
    response = _invoker(
        _runner(stdout=_envelope(result={"ok": True}, session_id="sess-xyz"))
    ).call("p", "en")
    assert response.session_id == "sess-xyz"


def test_call_returns_nonnegative_duration_s():
    response = _invoker(_runner(stdout=_envelope(result={"ok": True}))).call("p", "en")
    assert response.duration_s >= 0.0


def test_call_returns_claude_response_instance():
    response = _invoker(_runner(stdout=_envelope(result={"ok": True}))).call("p", "en")
    assert isinstance(response, ClaudeResponse)


# --- cache-read tokens ---


def test_cache_read_tokens_zero_when_field_absent():
    usage = {"input_tokens": 10, "output_tokens": 5}
    response = _invoker(
        _runner(stdout=_envelope(result={"ok": True}, usage=usage))
    ).call("p", "en")
    assert response.usage.cache_read_tokens == 0


def test_cache_read_tokens_populated_when_present():
    usage = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 999}
    response = _invoker(
        _runner(stdout=_envelope(result={"ok": True}, usage=usage))
    ).call("p", "en")
    assert response.usage.cache_read_tokens == 999


# --- usage limit ---


def test_usage_limit_envelope_raises_usage_limit_error():
    envelope = json.dumps(
        {"is_error": True, "result": "Claude AI usage limit reached", "usage": {}}
    )
    with pytest.raises(ClaudeUsageLimitError):
        _invoker(_runner(returncode=1, stdout=envelope)).call("p", "en")


def test_usage_limit_raised_even_on_zero_exit_code():
    envelope = json.dumps(
        {"is_error": True, "result": "usage limit reached", "usage": {}}
    )
    with pytest.raises(ClaudeUsageLimitError):
        _invoker(_runner(returncode=0, stdout=envelope)).call("p", "en")


def test_usage_limit_detection_is_case_insensitive():
    envelope = json.dumps(
        {"is_error": True, "result": "USAGE LIMIT EXCEEDED", "usage": {}}
    )
    with pytest.raises(ClaudeUsageLimitError):
        _invoker(_runner(returncode=1, stdout=envelope)).call("p", "en")


def test_rate_limit_phrase_also_raises_usage_limit_error():
    envelope = json.dumps(
        {
            "is_error": True,
            "result": "rate limit exceeded, try again later",
            "usage": {},
        }
    )
    with pytest.raises(ClaudeUsageLimitError):
        _invoker(_runner(returncode=1, stdout=envelope)).call("p", "en")


# --- non-zero exit without structured limit signal ---


def test_nonzero_exit_without_limit_signal_raises_cli_error():
    envelope = json.dumps(
        {"is_error": True, "result": "Something went wrong", "usage": {}}
    )
    with pytest.raises(ClaudeCliError):
        _invoker(_runner(returncode=1, stdout=envelope)).call("p", "en")


def test_cli_error_message_includes_exit_code():
    envelope = json.dumps({"is_error": True, "result": "Internal error", "usage": {}})
    with pytest.raises(ClaudeCliError, match="2"):
        _invoker(_runner(returncode=2, stdout=envelope)).call("p", "en")


def test_is_error_false_with_nonzero_exit_raises_cli_error():
    envelope = json.dumps(
        {
            "is_error": False,
            "result": json.dumps({"ok": True}),
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "total_cost_usd": 0.0,
            "session_id": "s",
        }
    )
    with pytest.raises(ClaudeCliError):
        _invoker(_runner(returncode=1, stdout=envelope)).call("p", "en")


# --- malformed envelope ---


def test_unparseable_envelope_json_raises_malformed_error():
    with pytest.raises(ClaudeMalformedEnvelopeError):
        _invoker(_runner(stdout="not json {{{")).call("p", "en")


def test_empty_stdout_raises_malformed_error():
    with pytest.raises(ClaudeMalformedEnvelopeError):
        _invoker(_runner(stdout="")).call("p", "en")


def test_malformed_result_field_raises_malformed_error():
    envelope = json.dumps(
        {
            "is_error": False,
            "result": "this is not json {{{",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 0.001,
            "session_id": "s",
        }
    )
    with pytest.raises(ClaudeMalformedEnvelopeError):
        _invoker(_runner(stdout=envelope)).call("p", "en")


def test_envelope_is_json_array_not_object_raises_malformed_error():
    with pytest.raises(ClaudeMalformedEnvelopeError):
        _invoker(_runner(stdout="[1, 2, 3]")).call("p", "en")


# --- large prompt round-trip ---


def test_large_prompt_delivered_to_runner_unchanged():
    calls: list[tuple[list[str], str]] = []
    large_prompt = "A" * 50_000
    _invoker(_runner(stdout=_envelope(result={"ok": True}), calls=calls)).call(
        large_prompt, "en"
    )
    assert len(calls) == 1
    _args, stdin_content = calls[0]
    assert stdin_content == large_prompt


def test_runner_receives_output_format_json_flag():
    calls: list[tuple[list[str], str]] = []
    _invoker(_runner(stdout=_envelope(result={"ok": True}), calls=calls)).call(
        "p", "en"
    )
    args, _ = calls[0]
    assert "--output-format" in args
    assert "json" in args
