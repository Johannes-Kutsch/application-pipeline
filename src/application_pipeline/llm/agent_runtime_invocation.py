from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

from agent_runtime import (
    AgentRuntimeError,
    RuntimeClient,
    RuntimeOutcome,
)
from agent_runtime import ToolPolicy
from agent_runtime.agent_log import AgentInvocationLog
from agent_runtime.runtime import EphemeralRunRequest, ProviderUsage
from agent_runtime.types import ProviderSelection

from .types import CallUsage

_AGENT_RUNTIME_SERVICE: Final = "opencode"
_AGENT_RUNTIME_MODEL: Final = "deepseek-v4-flash"
_AGENT_RUNTIME_EFFORT: Final = "medium"
_AGENT_RUNTIME_TOOL_POLICY: Final = ToolPolicy.NONE

_LLM_RUNTIME_LOG_DIR: Final = Path("llm") / "agent-runtime"
_CLASSIFY_LOG_SUBDIR: Final = "classify"
_JUDGE_LOG_SUBDIR: Final = "judge"

AgentRuntimeCallSiteName = Literal["classify", "judge"]

AgentRuntimeInvocationKind = Literal[
    "completed",
    "usage_limit",
    "retryable_provider_failure",
    "hard_provider_failure",
    "missing_usage",
]


@dataclass(frozen=True)
class AgentRuntimeInvocationResult:
    kind: AgentRuntimeInvocationKind
    output: str
    log_path: Path
    usage: CallUsage | None = None
    reset_time: datetime | None = None
    message: str | None = None


def _runtime_log_directory(
    logs_root: Path, call_site: AgentRuntimeCallSiteName
) -> Path:
    subdir = _CLASSIFY_LOG_SUBDIR if call_site == "classify" else _JUDGE_LOG_SUBDIR
    return logs_root / _LLM_RUNTIME_LOG_DIR / subdir


def _reserve_runtime_log_path(
    *, logs_root: Path, call_site: AgentRuntimeCallSiteName
) -> Path:
    log_dir = _runtime_log_directory(logs_root, call_site)
    return AgentInvocationLog().reserve(log_name=f"llm-{call_site}", logs_dir=log_dir)


def _build_request(prompt: str, invocation_dir: Path) -> EphemeralRunRequest:
    return EphemeralRunRequest(
        prompt=prompt,
        invocation_dir=invocation_dir,
        provider_selection=ProviderSelection(
            service=_AGENT_RUNTIME_SERVICE,
            model=_AGENT_RUNTIME_MODEL,
            effort=_AGENT_RUNTIME_EFFORT,
        ),
        tool_policy=_AGENT_RUNTIME_TOOL_POLICY,
    )


def _to_call_usage(usage: ProviderUsage | None) -> CallUsage | None:
    if usage is None:
        return None
    if (
        usage.input_tokens is None
        or usage.output_tokens is None
        or usage.cache_read_input_tokens is None
        or usage.cost_usd is None
        or usage.duration_seconds is None
    ):
        return None
    return CallUsage(
        input_tokens=int(usage.input_tokens),
        output_tokens=int(usage.output_tokens),
        cache_read_tokens=int(usage.cache_read_input_tokens),
        cost_usd=float(usage.cost_usd),
        duration_s=float(usage.duration_seconds),
    )


def _build_result(
    *,
    kind: AgentRuntimeInvocationKind,
    output: str,
    log_path: Path,
    usage: CallUsage | None,
    reset_time: datetime | None = None,
    message: str | None = None,
) -> AgentRuntimeInvocationResult:
    return AgentRuntimeInvocationResult(
        kind=kind,
        output=output,
        log_path=log_path,
        usage=usage,
        reset_time=reset_time,
        message=message,
    )


def _result_from_outcome(
    outcome: RuntimeOutcome, log_path: Path
) -> AgentRuntimeInvocationResult:
    usage = _to_call_usage(outcome.usage)
    if outcome.kind == "completed":
        if usage is None:
            return _build_result(
                kind="missing_usage",
                output=outcome.output,
                log_path=log_path,
                usage=None,
            )
        return _build_result(
            kind="completed",
            output=outcome.output,
            log_path=log_path,
            usage=usage,
        )
    if outcome.kind == "usage_limited":
        return _build_result(
            kind="usage_limit",
            output=outcome.output,
            log_path=log_path,
            usage=usage,
            reset_time=outcome.reset_time,
        )
    if outcome.kind == "retryable_provider_failure":
        return _build_result(
            kind="retryable_provider_failure",
            output=outcome.output,
            log_path=log_path,
            usage=usage,
        )
    return _build_result(
        kind="hard_provider_failure",
        output=outcome.output,
        log_path=log_path,
        usage=usage,
    )


def invoke_agent_runtime(
    prompt: str,
    *,
    logs_root: Path,
    call_site: AgentRuntimeCallSiteName,
) -> AgentRuntimeInvocationResult:
    log_path = _reserve_runtime_log_path(logs_root=logs_root, call_site=call_site)
    invocation_dir = log_path.with_suffix("")
    invocation_dir.mkdir(parents=True, exist_ok=True)
    request = _build_request(prompt=prompt, invocation_dir=invocation_dir)
    try:
        outcome = RuntimeClient().run_ephemeral(request)
    except AgentRuntimeError as exc:
        return _build_result(
            kind="hard_provider_failure",
            output="",
            log_path=log_path,
            usage=None,
            message=str(exc),
        )
    return _result_from_outcome(outcome, log_path=log_path)
