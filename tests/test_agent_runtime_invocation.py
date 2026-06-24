from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from agent_runtime.contracts import ToolAccess
from agent_runtime.runtime import (
    AgentEvent,
    EphemeralRunRequest,
    InvocationRecord,
    ProviderAuth,
    RuntimeOutcome,
)
from agent_runtime.session import RunKind

from application_pipeline.llm.agent_runtime_invocation import invoke_agent_runtime


@dataclass
class _CapturedRequest:
    prompt: str
    invocation_dir: Path
    service: str
    model: str
    effort: str
    auth: ProviderAuth | None
    tool_access: object


class _FakeRuntimeClient:
    outcome: RuntimeOutcome | None = None
    error: Exception | None = None
    requests: list[_CapturedRequest] = []

    def run_ephemeral(self, request: EphemeralRunRequest) -> RuntimeOutcome:
        self.requests.append(
            _CapturedRequest(
                prompt=request.prompt,
                invocation_dir=request.invocation_dir,
                service=request.provider_selection.service,
                model=request.provider_selection.model,
                effort=request.provider_selection.effort,
                auth=request.provider_selection.auth,
                tool_access=request.tool_access,
            )
        )
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        return self.outcome


@pytest.fixture(autouse=True)
def _use_fake_runtime_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeRuntimeClient.outcome = None
    _FakeRuntimeClient.error = None
    _FakeRuntimeClient.requests = []
    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation.RuntimeClient",
        _FakeRuntimeClient,
    )


@pytest.fixture
def logs_root(tmp_path: Path) -> Path:
    return tmp_path / ".runtime-data" / "logs"


def _event(text: str) -> AgentEvent:
    return AgentEvent(
        type="agent_message",
        service_name="opencode",
        raw_provider_output=text,
        text=text,
    )


def _record(
    *,
    provider_output: bytes | None = b"<verdict>{}</verdict>",
    events: tuple[AgentEvent, ...] = (),
    provider_session_id: str | None = "sess-1",
    outcome: str = "completed",
) -> InvocationRecord:
    return InvocationRecord(
        run_kind=RunKind.FRESH,
        service_name="opencode",
        model="deepseek-v4-flash",
        effort="medium",
        outcome=outcome,
        provider_session_id=provider_session_id,
        events=events,
        provider_output=provider_output,
        usage=None,
    )


def _completed(
    *,
    output: str = "<verdict>{}</verdict>",
    records: tuple[InvocationRecord, ...] | None = None,
) -> RuntimeOutcome:
    return RuntimeOutcome(
        kind="completed",
        output=output,
        invocation_records=records if records is not None else (_record(),),
    )


def test_completed_call_writes_per_call_evidence_directory(logs_root: Path) -> None:
    _FakeRuntimeClient.outcome = _completed()

    result = invoke_agent_runtime(
        "classify prompt", logs_root=logs_root, call_site="classify"
    )

    assert result.kind == "completed"
    assert result.output == "<verdict>{}</verdict>"
    evidence_dir = result.evidence_dir
    assert evidence_dir.parent == logs_root / "llm" / "agent-runtime" / "classify"
    assert evidence_dir.is_dir()
    assert (evidence_dir / "prompt").is_file()
    assert (evidence_dir / "response").is_file()
    assert (evidence_dir / "events").is_file()
    assert (evidence_dir / "meta").is_file()


def test_evidence_files_carry_prompt_response_events_and_meta(
    logs_root: Path,
) -> None:
    _FakeRuntimeClient.outcome = _completed(
        records=(
            _record(
                provider_output=b"<verdict>{}</verdict>",
                events=(_event("thinking"), _event("done")),
                provider_session_id="sess-xyz",
            ),
        ),
    )

    result = invoke_agent_runtime(
        "the sent prompt", logs_root=logs_root, call_site="classify"
    )
    evidence_dir = result.evidence_dir

    assert (evidence_dir / "prompt").read_text(encoding="utf-8") == "the sent prompt"
    assert (
        (evidence_dir / "response").read_text(encoding="utf-8")
        == "<verdict>{}</verdict>"
    )
    events_text = (evidence_dir / "events").read_text(encoding="utf-8")
    assert "thinking" in events_text
    assert "done" in events_text
    meta_text = (evidence_dir / "meta").read_text(encoding="utf-8")
    assert "sess-xyz" in meta_text


def test_judge_call_writes_under_judge_subdir(logs_root: Path) -> None:
    _FakeRuntimeClient.outcome = _completed(output="[]")

    result = invoke_agent_runtime("judge prompt", logs_root=logs_root, call_site="judge")

    assert result.evidence_dir.parent == logs_root / "llm" / "agent-runtime" / "judge"


def test_request_uses_no_tools_and_worktree_outside_logs_root(
    logs_root: Path,
) -> None:
    _FakeRuntimeClient.outcome = _completed()

    invoke_agent_runtime("classify prompt", logs_root=logs_root, call_site="classify")

    captured = _FakeRuntimeClient.requests[0]
    assert captured.tool_access == ToolAccess.no_tools()
    assert logs_root not in captured.invocation_dir.parents


def test_explicit_provider_auth_is_forwarded(logs_root: Path) -> None:
    _FakeRuntimeClient.outcome = _completed(output="[]")
    provider_auth = ProviderAuth(opencode_api_key="test-key")

    invoke_agent_runtime(
        "judge prompt",
        logs_root=logs_root,
        call_site="judge",
        provider_auth=provider_auth,
    )

    assert _FakeRuntimeClient.requests[0].auth == provider_auth


def test_multiple_records_are_index_suffixed_in_one_directory(
    logs_root: Path,
) -> None:
    _FakeRuntimeClient.outcome = _completed(
        records=(
            _record(provider_output=b"first", provider_session_id="s0"),
            _record(provider_output=b"second", provider_session_id="s1"),
        ),
    )

    result = invoke_agent_runtime(
        "classify prompt", logs_root=logs_root, call_site="classify"
    )
    evidence_dir = result.evidence_dir

    assert (evidence_dir / "response").read_text(encoding="utf-8") == "first"
    assert (evidence_dir / "response.1").read_text(encoding="utf-8") == "second"
    assert (evidence_dir / "meta.1").is_file()


def test_completed_call_with_no_records_is_a_diagnostic_gap_not_a_failure(
    logs_root: Path,
) -> None:
    _FakeRuntimeClient.outcome = _completed(output="payload", records=())

    result = invoke_agent_runtime(
        "classify prompt", logs_root=logs_root, call_site="classify"
    )

    assert result.kind == "completed"
    assert result.output == "payload"
    # Directory exists for the pointer, but response evidence is empty.
    assert (result.evidence_dir / "response").read_text(encoding="utf-8") == ""


def test_serialization_write_error_does_not_change_kind_or_output(
    logs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeRuntimeClient.outcome = _completed(output="payload")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation._write_evidence", boom
    )

    result = invoke_agent_runtime(
        "classify prompt", logs_root=logs_root, call_site="classify"
    )

    assert result.kind == "completed"
    assert result.output == "payload"


def test_usage_limited_outcome_reports_reset_time(logs_root: Path) -> None:
    from datetime import datetime, timezone

    reset_time = datetime(2026, 6, 22, 8, 45, tzinfo=timezone.utc)
    _FakeRuntimeClient.outcome = RuntimeOutcome(
        kind="usage_limited", output="partial", reset_time=reset_time
    )

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="judge")

    assert result.kind == "usage_limit"
    assert result.output == "partial"
    assert result.reset_time == reset_time


def test_retryable_provider_failure_is_reported(logs_root: Path) -> None:
    _FakeRuntimeClient.outcome = RuntimeOutcome(
        kind="retryable_provider_failure", output="provider unavailable"
    )

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="classify")

    assert result.kind == "retryable_provider_failure"
    assert result.output == "provider unavailable"


def test_hard_provider_failure_from_runtime_error(logs_root: Path) -> None:
    from agent_runtime import AgentRuntimeError

    _FakeRuntimeClient.error = AgentRuntimeError("provider exploded")

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="judge")

    assert result.kind == "hard_provider_failure"
    assert result.output == ""
    assert result.message == "provider exploded"
