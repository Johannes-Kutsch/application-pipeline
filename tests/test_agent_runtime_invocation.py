from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from agent_runtime import AgentRuntimeError, ToolPolicy
from agent_runtime.runtime import (
    EphemeralRunRequest,
    ProviderAuth,
    ProviderUsage,
    RuntimeOutcome,
)

from application_pipeline.llm.agent_runtime_invocation import invoke_agent_runtime


@dataclass
class _CapturedRequest:
    prompt: str
    invocation_dir: Path
    service: str
    model: str
    effort: str
    auth: ProviderAuth | None
    tool_policy: ToolPolicy | object


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
                tool_policy=request.tool_policy,
            )
        )
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        return self.outcome


@pytest.fixture(autouse=True)
def _reset_fake_runtime_client() -> None:
    _FakeRuntimeClient.outcome = None
    _FakeRuntimeClient.error = None
    _FakeRuntimeClient.requests = []


@pytest.fixture
def logs_root(tmp_path: Path) -> Path:
    return tmp_path / ".runtime-data" / "logs"


def test_invoke_agent_runtime_completes_with_pinned_project_decisions(
    monkeypatch: pytest.MonkeyPatch, logs_root: Path
) -> None:
    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation.RuntimeClient",
        _FakeRuntimeClient,
    )
    _FakeRuntimeClient.outcome = RuntimeOutcome(
        kind="completed",
        output="<verdict>{}</verdict>",
        usage=ProviderUsage(
            input_tokens=11,
            output_tokens=7,
            cache_read_input_tokens=3,
            cost_usd=0.25,
            duration_seconds=1.5,
        ),
    )

    result = invoke_agent_runtime(
        "classify prompt",
        logs_root=logs_root,
        call_site="classify",
    )

    assert result.kind == "completed"
    assert result.output == "<verdict>{}</verdict>"
    assert result.message is None
    assert result.reset_time is None
    assert result.usage is not None
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    assert result.usage.cache_read_tokens == 3
    assert result.log_path.parent == logs_root / "llm" / "agent-runtime" / "classify"
    assert result.log_path.suffix == ".log"
    assert result.log_path.exists()
    assert _FakeRuntimeClient.requests == [
        _CapturedRequest(
            prompt="classify prompt",
            invocation_dir=result.log_path.with_suffix(""),
            service="opencode",
            model="deepseek-v4-flash",
            effort="medium",
            auth=None,
            tool_policy=ToolPolicy.NONE,
        )
    ]
    assert result.log_path.with_suffix("").is_dir()


def test_invoke_agent_runtime_forwards_explicit_provider_auth(
    monkeypatch: pytest.MonkeyPatch, logs_root: Path
) -> None:
    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation.RuntimeClient",
        _FakeRuntimeClient,
    )
    _FakeRuntimeClient.outcome = RuntimeOutcome(
        kind="completed",
        output="[]",
        usage=ProviderUsage(
            input_tokens=2,
            output_tokens=3,
            cache_read_input_tokens=0,
            cost_usd=0.02,
            duration_seconds=0.4,
        ),
    )
    provider_auth = ProviderAuth(opencode_api_key="test-key")

    invoke_agent_runtime(
        "judge prompt",
        logs_root=logs_root,
        call_site="judge",
        provider_auth=provider_auth,
    )

    assert _FakeRuntimeClient.requests[0].auth == provider_auth


def test_invoke_agent_runtime_reserves_one_judge_log_path_per_invocation(
    monkeypatch: pytest.MonkeyPatch, logs_root: Path
) -> None:
    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation.RuntimeClient",
        _FakeRuntimeClient,
    )
    _FakeRuntimeClient.outcome = RuntimeOutcome(
        kind="completed",
        output="[]",
        usage=ProviderUsage(
            input_tokens=1,
            output_tokens=2,
            cache_read_input_tokens=0,
            cost_usd=0.01,
            duration_seconds=0.5,
        ),
    )

    first = invoke_agent_runtime("judge prompt", logs_root=logs_root, call_site="judge")
    second = invoke_agent_runtime(
        "judge prompt again", logs_root=logs_root, call_site="judge"
    )

    assert first.log_path.parent == logs_root / "llm" / "agent-runtime" / "judge"
    assert second.log_path.parent == first.log_path.parent
    assert second.log_path != first.log_path
    assert first.log_path.exists()
    assert second.log_path.exists()


def test_invoke_agent_runtime_completed_without_usage_stays_completed(
    monkeypatch: pytest.MonkeyPatch, logs_root: Path
) -> None:
    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation.RuntimeClient",
        _FakeRuntimeClient,
    )
    _FakeRuntimeClient.outcome = RuntimeOutcome(kind="completed", output="payload")

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="classify")

    assert result.kind == "completed"
    assert result.output == "payload"
    assert result.usage is None


def test_invoke_agent_runtime_reports_usage_limit_reset_time(
    monkeypatch: pytest.MonkeyPatch, logs_root: Path
) -> None:
    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation.RuntimeClient",
        _FakeRuntimeClient,
    )
    reset_time = datetime(2026, 6, 22, 8, 45, tzinfo=timezone.utc)
    _FakeRuntimeClient.outcome = RuntimeOutcome(
        kind="usage_limited",
        output="partial",
        reset_time=reset_time,
    )

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="judge")

    assert result.kind == "usage_limit"
    assert result.output == "partial"
    assert result.reset_time == reset_time


def test_invoke_agent_runtime_reports_retryable_provider_failure(
    monkeypatch: pytest.MonkeyPatch, logs_root: Path
) -> None:
    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation.RuntimeClient",
        _FakeRuntimeClient,
    )
    _FakeRuntimeClient.outcome = RuntimeOutcome(
        kind="retryable_provider_failure",
        output="provider unavailable",
    )

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="classify")

    assert result.kind == "retryable_provider_failure"
    assert result.output == "provider unavailable"
    assert result.message is None


def test_invoke_agent_runtime_reports_hard_provider_failure_from_runtime_error(
    monkeypatch: pytest.MonkeyPatch, logs_root: Path
) -> None:
    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation.RuntimeClient",
        _FakeRuntimeClient,
    )
    _FakeRuntimeClient.error = AgentRuntimeError("provider exploded")

    result = invoke_agent_runtime("prompt", logs_root=logs_root, call_site="judge")

    assert result.kind == "hard_provider_failure"
    assert result.output == ""
    assert result.usage is None
    assert result.message == "provider exploded"
