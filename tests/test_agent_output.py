"""Protocol-level tests for extract_json_block and AgentOutputProtocolError."""

from __future__ import annotations

import json

import pytest

from application_pipeline.llm.agent_output import (
    AgentOutputProtocolError,
    extract_json_block,
)


# ---------------------------------------------------------------------------
# extract_json_block: naked JSON in tag
# ---------------------------------------------------------------------------


def test_extract_json_block_returns_parsed_naked_json() -> None:
    payload = [{"id": "1", "in_domain": True}]
    text = f"<verdicts>{json.dumps(payload)}</verdicts>"
    result, is_fallback = extract_json_block(text, "verdicts")
    assert result == payload
    assert is_fallback is False


def test_extract_json_block_returns_parsed_json_fence_with_lang() -> None:
    payload = [{"id": "1", "in_domain": True}]
    body = json.dumps(payload)
    text = f"<verdicts>\n```json\n{body}\n```\n</verdicts>"
    result, is_fallback = extract_json_block(text, "verdicts")
    assert result == payload
    assert is_fallback is False


def test_extract_json_block_returns_parsed_json_bare_fence() -> None:
    payload = [{"id": "2", "in_domain": False}]
    body = json.dumps(payload)
    text = f"<verdicts>\n```\n{body}\n```\n</verdicts>"
    result, is_fallback = extract_json_block(text, "verdicts")
    assert result == payload
    assert is_fallback is False


def test_extract_json_block_ignores_preamble_before_tag() -> None:
    payload = {"tier": "green", "matched": ["python"], "missing": [], "summary": "ok"}
    body = json.dumps(payload)
    text = f"Sure, here is my answer:\n\n<verdict>{body}</verdict>"
    result, is_fallback = extract_json_block(text, "verdict")
    assert result == payload
    assert is_fallback is False


def test_extract_json_block_handles_stray_opening_tag_in_body() -> None:
    """Walk-back recovers when the JSON body contains a stray copy of the opening tag."""
    inner = json.dumps([{"id": "<verdicts>stray", "in_domain": True}])
    text = f"<verdicts>{inner}</verdicts>"
    result, _ = extract_json_block(text, "verdicts")
    assert result[0]["id"] == "<verdicts>stray"


# ---------------------------------------------------------------------------
# extract_json_block: bare-fence fallback (tags absent)
# ---------------------------------------------------------------------------


def test_extract_json_block_bare_fence_fallback_returns_json_and_signals_fallback() -> (
    None
):
    payload = {"matches": False}
    body = json.dumps(payload)
    text = f"```json\n{body}\n```"
    result, is_fallback = extract_json_block(text, "verdict")
    assert result == payload
    assert is_fallback is True


# ---------------------------------------------------------------------------
# extract_json_block: tag_missing
# ---------------------------------------------------------------------------


def test_extract_json_block_raises_tag_missing_when_tag_absent() -> None:
    with pytest.raises(AgentOutputProtocolError) as exc_info:
        extract_json_block('{"not": "wrapped"}', "verdicts")
    assert exc_info.value.kind == "tag_missing"


def test_extract_json_block_raises_tag_missing_when_only_closing_tag() -> None:
    with pytest.raises(AgentOutputProtocolError) as exc_info:
        extract_json_block("some text</verdicts>", "verdicts")
    assert exc_info.value.kind == "tag_missing"


# ---------------------------------------------------------------------------
# extract_json_block: json_malformed
# ---------------------------------------------------------------------------


def test_extract_json_block_raises_json_malformed_when_body_invalid() -> None:
    with pytest.raises(AgentOutputProtocolError) as exc_info:
        extract_json_block("<verdicts>not valid json</verdicts>", "verdicts")
    assert exc_info.value.kind == "json_malformed"


def test_extract_json_block_raises_json_malformed_when_all_candidates_fail() -> None:
    """All openers produce unparseable bodies → json_malformed."""
    text = "<verdicts>bad<verdicts>also bad</verdicts>"
    with pytest.raises(AgentOutputProtocolError) as exc_info:
        extract_json_block(text, "verdicts")
    assert exc_info.value.kind == "json_malformed"


# ---------------------------------------------------------------------------
# AgentOutputProtocolError shape
# ---------------------------------------------------------------------------


def test_agent_output_protocol_error_has_kind_tag_missing() -> None:
    exc = AgentOutputProtocolError("tag_missing")
    assert exc.kind == "tag_missing"


def test_agent_output_protocol_error_has_kind_json_malformed() -> None:
    exc = AgentOutputProtocolError("json_malformed")
    assert exc.kind == "json_malformed"
