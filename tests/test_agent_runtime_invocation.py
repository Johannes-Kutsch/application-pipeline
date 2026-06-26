from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from agent_runtime.contracts import ToolAccess
from agent_runtime.runtime import (
    AgentEvent,
    Cancelled,
    Completed,
    EphemeralRunRequest,
    ProviderAuth,
    ProviderUnavailable,
    ProviderUsage,
    RunResult,
    RuntimeOutcome,
    TimedOut,
    UsageLimited,
)
from agent_runtime.errors import ProviderUnavailableReason
from agent_runtime.types import ResolvedProvider

from typing import Literal

from application_pipeline.llm.agent_runtime_invocation import (
    AgentRuntimeInvocationAdapter,
    AgentRuntimeInvocationResult,
    invoke_agent_runtime,
)


@dataclass
class _CapturedRequest:
    prompt: str
    invocation_dir: Path
    service: str
    model: str
    effort: str
    auth: ProviderAuth | None
    tool_access: object
    callback_count: int


class _FakeRuntimeClient:
    outcome: RuntimeOutcome | None = None
    events: tuple[AgentEvent, ...] = ()
    error: Exception | None = None
    prompt_encoding: str | None = None
    requests: list[_CapturedRequest] = []

    async def run_ephemeral(self, request: EphemeralRunRequest) -> RuntimeOutcome:
        callback_count = 0
        if request.on_live_output is not None:
            for event in self.events:
                request.on_live_output(event)
                callback_count += 1
        if self.prompt_encoding is not None:
            request.prompt.encode(self.prompt_encoding)
        self.requests.append(
            _CapturedRequest(
                prompt=request.prompt,
                invocation_dir=request.invocation_dir,
                service=request.provider_selection.service,
                model=request.provider_selection.model,
                effort=request.provider_selection.effort,
                auth=request.provider_selection.auth,
                tool_access=request.tool_access,
                callback_count=callback_count,
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
    _FakeRuntimeClient.events = ()
    _FakeRuntimeClient.prompt_encoding = None
    _FakeRuntimeClient.requests = []
    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation.RuntimeClient",
        _FakeRuntimeClient,
    )


@pytest.fixture
def logs_root(tmp_path: Path) -> Path:
    return tmp_path / ".runtime-data" / "logs"


def _event(display_message: str) -> AgentEvent:
    return AgentEvent(
        type="agent_message",
        display_message=display_message,
        raw_provider_output=display_message,
    )


def _run_result(
    *,
    output: str = "<verdict>{}</verdict>",
    usage: ProviderUsage | None = None,
    selected: ResolvedProvider | None = None,
) -> RunResult:
    return RunResult(
        output=output,
        usage=usage,
        continuation=None,
        selected=selected
        or ResolvedProvider(
            service="opencode", model="deepseek-v4-flash", effort="medium"
        ),
    )


def _completed(
    *,
    output: str = "<verdict>{}</verdict>",
    usage: ProviderUsage | None = None,
    selected: ResolvedProvider | None = None,
) -> RuntimeOutcome:
    return RuntimeOutcome(
        kind=Completed(),
        result=_run_result(output=output, usage=usage, selected=selected),
    )


def _usage_limited(
    *,
    output: str = "<verdict>{}</verdict>",
    usage: ProviderUsage | None = None,
    reset_time: datetime,
) -> RuntimeOutcome:
    return RuntimeOutcome(
        kind=UsageLimited(reset_time=reset_time),
        result=_run_result(output=output, usage=usage),
    )


def _provider_unavailable(
    *,
    detail: str = "provider unavailable",
    reason: ProviderUnavailableReason = ProviderUnavailableReason.TRANSIENT_API_ERROR,
) -> RuntimeOutcome:
    return RuntimeOutcome(
        kind=ProviderUnavailable(reason=reason, detail=detail),
        result=_run_result(),
    )


def _cancelled(detail: str = "cancelled") -> RuntimeOutcome:
    return RuntimeOutcome(
        kind=Cancelled(),
        result=_run_result(output=detail),
    )


def _timed_out(detail: str = "timed out") -> RuntimeOutcome:
    return RuntimeOutcome(
        kind=TimedOut(),
        result=_run_result(output=detail),
    )


def test_completed_call_writes_one_invocation_log_file(logs_root: Path) -> None:
    _FakeRuntimeClient.outcome = _completed()

    result = invoke_agent_runtime(
        "classify prompt", logs_root=logs_root, call_site="classify"
    )
    judge_result = invoke_agent_runtime(
        "judge prompt", logs_root=logs_root, call_site="judge"
    )

    assert result.kind == "completed"
    assert result.output == "<verdict>{}</verdict>"
    assert (
        result.evidence_path.parent == logs_root / "llm" / "agent-runtime" / "classify"
    )
    assert result.evidence_path.suffix == ".log"
    assert result.evidence_path.is_file()

    assert judge_result.kind == "completed"
    assert (
        judge_result.evidence_path.parent
        == logs_root / "llm" / "agent-runtime" / "judge"
    )
    assert judge_result.evidence_path.suffix == ".log"
    assert judge_result.evidence_path.is_file()


def test_log_file_has_prompt_events_and_result_sections(
    logs_root: Path,
) -> None:
    usage = ProviderUsage(
        input_tokens=12,
        output_tokens=3,
        cache_read_input_tokens=1,
    )
    _FakeRuntimeClient.events = (_event("thinking"), _event("done"))
    _FakeRuntimeClient.outcome = _completed(usage=usage)

    result = invoke_agent_runtime(
        "the sent prompt", logs_root=logs_root, call_site="judge"
    )
    content = result.evidence_path.read_text(encoding="utf-8")

    assert content.startswith("[prompt]\nthe sent prompt\n")
    assert "[events]" in content
    assert "agent_message | thinking" in content
    assert "raw_provider_output=thinking" in content
    assert "agent_message | done" in content
    assert "[result]" in content
    assert "outcome=completed" in content
    assert "service=opencode" in content
    assert "model=deepseek-v4-flash" in content
    assert "effort=medium" in content
    assert "usage=ProviderUsage(" in content


def test_request_construction_uses_pinned_provider_no_tools_and_worktree_outside_logs_root(
    logs_root: Path,
) -> None:
    _FakeRuntimeClient.outcome = _completed()

    invoke_agent_runtime("classify prompt", logs_root=logs_root, call_site="classify")

    captured = _FakeRuntimeClient.requests[0]
    assert captured.prompt == "classify prompt"
    assert captured.service == "opencode"
    assert captured.model == "deepseek-v4-flash"
    assert captured.effort == "medium"
    assert captured.tool_access == ToolAccess.no_tools()
    assert logs_root not in captured.invocation_dir.parents
    assert captured.callback_count == 0


_UNICODE_SPACE_SEPARATORS = (
    "\u0020",
    "\u00a0",
    "\u1680",
    "\u2000",
    "\u2001",
    "\u2002",
    "\u2003",
    "\u2004",
    "\u2005",
    "\u2006",
    "\u2007",
    "\u2008",
    "\u2009",
    "\u200a",
    "\u202f",
    "\u205f",
    "\u3000",
)


@pytest.mark.parametrize("call_site", ["classify", "judge"])
def test_agent_runtime_invocation_normalizes_unicode_space_separators_for_windows_stdio(
    logs_root: Path, call_site: Literal["classify", "judge"]
) -> None:
    _FakeRuntimeClient.outcome = _completed()
    _FakeRuntimeClient.prompt_encoding = "cp1252"
    prompt = "Before\u202fAfter"

    result = invoke_agent_runtime(prompt, logs_root=logs_root, call_site=call_site)

    assert result.kind == "completed"
    assert _FakeRuntimeClient.requests[0].prompt == "Before After"
    assert "[result]" in result.evidence_path.read_text(encoding="utf-8")


def test_agent_runtime_invocation_drops_directional_format_characters(
    logs_root: Path,
) -> None:
    _FakeRuntimeClient.outcome = _completed()
    _FakeRuntimeClient.prompt_encoding = "cp1252"
    prompt = "Before\u200eMiddle\u200fAfter"

    invoke_agent_runtime(prompt, logs_root=logs_root, call_site="judge")

    assert _FakeRuntimeClient.requests[0].prompt == "BeforeMiddleAfter"


def test_agent_runtime_invocation_rewrites_unicode_space_separators_and_drops_zero_width_format_characters(
    logs_root: Path,
) -> None:
    _FakeRuntimeClient.outcome = _completed()
    _FakeRuntimeClient.prompt_encoding = "cp1252"
    prompt = f"A{''.join(_UNICODE_SPACE_SEPARATORS)}B\tC\nD\u200bE"

    invoke_agent_runtime(prompt, logs_root=logs_root, call_site="classify")

    assert _FakeRuntimeClient.requests[0].prompt == f"A{' ' * 17}B\tC\nDE"


def test_agent_runtime_invocation_replaces_cp1252_unencodable_dash_with_ascii_hyphen(
    logs_root: Path,
) -> None:
    _FakeRuntimeClient.outcome = _completed()
    _FakeRuntimeClient.prompt_encoding = "cp1252"
    prompt = "non‑breaking"

    invoke_agent_runtime(prompt, logs_root=logs_root, call_site="classify")

    assert _FakeRuntimeClient.requests[0].prompt == "non-breaking"


def test_agent_runtime_invocation_preserves_cp1252_encodable_umlauts(
    logs_root: Path,
) -> None:
    _FakeRuntimeClient.outcome = _completed()
    _FakeRuntimeClient.prompt_encoding = "cp1252"
    prompt = "Fähre, Öl, Überblick, Straße"

    invoke_agent_runtime(prompt, logs_root=logs_root, call_site="classify")

    assert _FakeRuntimeClient.requests[0].prompt == prompt


@pytest.mark.parametrize("call_site", ["classify", "judge"])
def test_explicit_provider_auth_is_forwarded(
    logs_root: Path, call_site: Literal["classify", "judge"]
) -> None:
    _FakeRuntimeClient.outcome = _completed(output="[]")
    provider_auth = ProviderAuth(opencode_api_key="test-key")

    invoke_agent_runtime(
        f"{call_site} prompt",
        logs_root=logs_root,
        call_site=call_site,
        provider_auth=provider_auth,
    )

    assert _FakeRuntimeClient.requests[0].auth is provider_auth


def test_agent_runtime_invocation_adapter_delegates_call_shape() -> None:
    captured: dict[str, object] = {}
    expected = AgentRuntimeInvocationResult(
        kind="completed",
        output="payload",
        evidence_path=Path("llm/classify/llm-classify-1"),
    )

    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: str,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        captured["prompt"] = prompt
        captured["logs_root"] = logs_root
        captured["call_site"] = call_site
        captured["provider_auth"] = provider_auth
        return expected

    provider_auth = ProviderAuth(opencode_api_key="operator-key")
    adapter = AgentRuntimeInvocationAdapter(invoke=_fake_invoke)

    result = adapter.invoke(
        "judge prompt",
        logs_root=Path("/tmp/run-log"),
        call_site="judge",
        provider_auth=provider_auth,
    )

    assert result == expected
    assert captured == {
        "prompt": "judge prompt",
        "logs_root": Path("/tmp/run-log"),
        "call_site": "judge",
        "provider_auth": provider_auth,
    }


def test_usage_limited_outcome_reports_reset_time(logs_root: Path) -> None:
    reset_time = datetime(2026, 6, 22, 8, 45, tzinfo=timezone.utc)
    _FakeRuntimeClient.outcome = _usage_limited(output="partial", reset_time=reset_time)

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="judge")

    assert result.kind == "usage_limit"
    assert result.output == "partial"
    assert result.reset_time == reset_time
    assert result.evidence_path.is_file()
    assert "[result]\noutcome=usage_limited" in result.evidence_path.read_text(
        encoding="utf-8"
    )


def test_retryable_provider_failure_is_reported(logs_root: Path) -> None:
    _FakeRuntimeClient.outcome = _provider_unavailable()

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="classify")

    assert result.kind == "retryable_provider_failure"
    assert result.output == ""
    assert result.message == "provider unavailable"
    assert result.evidence_path.is_file()


def test_provider_not_available_is_hard_provider_failure(logs_root: Path) -> None:
    _FakeRuntimeClient.outcome = _provider_unavailable(
        reason=ProviderUnavailableReason.SERVICE_NOT_AVAILABLE
    )

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="classify")

    assert result.kind == "hard_provider_failure"
    assert result.output == ""
    assert result.message == "provider unavailable"


def test_hard_agent_error_propagates_from_runtime_boundary(logs_root: Path) -> None:
    from agent_runtime import HardAgentError

    _FakeRuntimeClient.error = HardAgentError("provider exploded")

    with pytest.raises(HardAgentError, match="provider exploded"):
        invoke_agent_runtime("prompt", logs_root=logs_root, call_site="judge")


def test_cancelled_and_timed_out_map_to_hard_provider_failure(logs_root: Path) -> None:
    _FakeRuntimeClient.outcome = _cancelled("cancelled during run")

    cancelled = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="judge")

    assert cancelled.kind == "hard_provider_failure"
    assert cancelled.output == "cancelled during run"
    assert "cancelled" in (cancelled.message or "")

    _FakeRuntimeClient.outcome = _timed_out("timed out")

    timed_out = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="judge")

    assert timed_out.kind == "hard_provider_failure"
    assert timed_out.output == "timed out"
    assert "timed out" in (timed_out.message or "")


def test_log_file_records_non_default_selected_provider(logs_root: Path) -> None:
    selected = _run_result(
        selected=ResolvedProvider(service="opencode", model="gpt-5", effort="high")
    )
    _FakeRuntimeClient.outcome = _completed(selected=selected.selected)

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="classify")

    content = result.evidence_path.read_text(encoding="utf-8")
    assert "service=opencode" in content
    assert "model=gpt-5" in content
    assert "effort=high" in content


def test_result_section_uses_plain_service_model_effort_fields(logs_root: Path) -> None:
    _FakeRuntimeClient.outcome = _completed()

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="judge")
    content = result.evidence_path.read_text(encoding="utf-8")

    assert "outcome=completed" in content
    assert "service=opencode" in content
    assert "model=deepseek-v4-flash" in content
    assert "effort=medium" in content
    assert "selected_service=" not in content
    assert "selected_model=" not in content
    assert "selected_effort=" not in content


def test_events_append_during_live_invocation(
    logs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeRuntimeClient.outcome = _completed()

    writes: list[tuple[Path, str]] = []

    import application_pipeline.llm.agent_runtime_invocation as adapter

    original_append = adapter._safe_append

    def recording_append(path: Path, payload: str) -> None:
        writes.append((path, payload))
        original_append(path, payload)

    async def run_with_assertions(
        self: object, request: EphemeralRunRequest
    ) -> RuntimeOutcome:
        assert request.on_live_output is not None
        request.on_live_output(_event("first"))
        request.on_live_output(_event("second"))

        evidence = next(
            path
            for path, line in writes
            if path.suffix == ".log" and line.startswith("[events]")
        )
        assert any(
            path == evidence
            and line == "agent_message | first | raw_provider_output=first\n"
            for path, line in writes
        )
        assert any(
            path == evidence
            and line == "agent_message | second | raw_provider_output=second\n"
            for path, line in writes
        )
        assert not any(
            path == evidence and line.startswith("[result]") for path, line in writes
        )

        assert _FakeRuntimeClient.outcome is not None
        return _FakeRuntimeClient.outcome

    monkeypatch.setattr(adapter, "_safe_append", recording_append)
    monkeypatch.setattr(_FakeRuntimeClient, "run_ephemeral", run_with_assertions)

    result = invoke_agent_runtime(
        "stream prompt", logs_root=logs_root, call_site="judge"
    )

    assert result.kind == "completed"


def test_serialization_write_error_does_not_change_kind_or_output(
    logs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeRuntimeClient.outcome = _completed(output="payload")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    import application_pipeline.llm.agent_runtime_invocation as adapter

    monkeypatch.setattr(adapter, "_safe_append", boom)

    result = invoke_agent_runtime(
        "classify prompt", logs_root=logs_root, call_site="classify"
    )

    assert result.kind == "completed"
    assert result.output == "payload"
