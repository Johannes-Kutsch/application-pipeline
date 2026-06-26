from __future__ import annotations

import asyncio
import tempfile
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, Protocol

from agent_runtime import AgentRuntimeError, RuntimeClient, RuntimeOutcome
from agent_runtime.contracts import ToolAccess
from agent_runtime.runtime import (
    AgentEvent,
    Cancelled,
    Completed,
    EphemeralRunRequest,
    ProviderAuth,
    ProviderUnavailable,
    RunResult,
    ResolvedProvider,
    TimedOut,
    UsageLimited,
)
from agent_runtime.errors import ProviderUnavailableReason
from agent_runtime.types import ProviderSelection


_AGENT_RUNTIME_SERVICE: Final = "opencode"
_AGENT_RUNTIME_MODEL: Final = "deepseek-v4-flash"
_AGENT_RUNTIME_EFFORT: Final = "medium"

_LLM_RUNTIME_LOG_DIR: Final = Path("llm") / "agent-runtime"
_CLASSIFY_LOG_SUBDIR: Final = "classify"
_JUDGE_LOG_SUBDIR: Final = "judge"

AgentRuntimeCallSiteName = Literal["classify", "judge"]

AgentRuntimeInvocationKind = Literal[
    "completed",
    "usage_limit",
    "retryable_provider_failure",
    "hard_provider_failure",
]


@dataclass(frozen=True)
class AgentRuntimeInvocationResult:
    kind: AgentRuntimeInvocationKind
    output: str
    evidence_path: Path
    reset_time: datetime | None = None
    message: str | None = None


class AgentRuntimeInvocationPort(Protocol):
    @abstractmethod
    def invoke(
        self,
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult: ...


class AgentRuntimeInvocationAdapter:
    def __init__(
        self,
        invoke: Callable[..., AgentRuntimeInvocationResult] | None = None,
    ) -> None:
        self._invoke = invoke if invoke is not None else invoke_agent_runtime

    def invoke(
        self,
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        return self._invoke(
            prompt,
            logs_root=logs_root,
            call_site=call_site,
            provider_auth=provider_auth,
        )


def _evidence_parent(logs_root: Path, call_site: AgentRuntimeCallSiteName) -> Path:
    subdir = _CLASSIFY_LOG_SUBDIR if call_site == "classify" else _JUDGE_LOG_SUBDIR
    return logs_root / _LLM_RUNTIME_LOG_DIR / subdir


def _new_evidence_path(logs_root: Path, call_site: AgentRuntimeCallSiteName) -> Path:
    parent = _evidence_parent(logs_root, call_site)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S-%f")
    return parent / f"llm-{call_site}-{stamp}.log"


def _build_request(
    prompt: str,
    invocation_dir: Path,
    provider_auth: ProviderAuth | None,
    on_live_output: Callable[[AgentEvent], None] | None = None,
) -> EphemeralRunRequest:
    return EphemeralRunRequest(
        prompt=prompt,
        invocation_dir=invocation_dir,
        provider_selection=ProviderSelection(
            service=_AGENT_RUNTIME_SERVICE,
            model=_AGENT_RUNTIME_MODEL,
            effort=_AGENT_RUNTIME_EFFORT,
            auth=provider_auth,
        ),
        tool_access=ToolAccess.no_tools(),
        timeout_seconds=1200,
        on_live_output=on_live_output,
    )


def _safe_append(path: Path, payload: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(payload)


def _persist_prompt(path: Path, prompt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_append(path, "[prompt]\n")
    _safe_append(path, prompt)
    if not prompt.endswith("\n"):
        _safe_append(path, "\n")


def _persist_events_header(path: Path) -> None:
    _safe_append(path, "[events]\n")


def _render_event(event: AgentEvent) -> str:
    parts = [event.type, event.display_message]
    if event.raw_provider_output:
        parts.append(f"raw_provider_output={event.raw_provider_output}")
    return " | ".join(parts)


def _persist_result(
    path: Path,
    *,
    selected: ResolvedProvider | None,
    usage: object | None,
    kind_name: str,
) -> None:
    _safe_append(path, "[result]\n")
    _safe_append(path, f"outcome={kind_name}\n")
    if selected is not None:
        _safe_append(path, f"service={selected.service}\n")
        _safe_append(path, f"model={selected.model}\n")
        _safe_append(path, f"effort={selected.effort}\n")
    if usage is not None:
        _safe_append(path, f"usage={usage!r}\n")


def _result_section_kind(outcome: RuntimeOutcome) -> str:
    if isinstance(outcome.kind, Completed):
        return "completed"
    if isinstance(outcome.kind, UsageLimited):
        return "usage_limited"
    if isinstance(outcome.kind, ProviderUnavailable):
        return "provider_unavailable"
    if isinstance(outcome.kind, Cancelled):
        return "cancelled"
    if isinstance(outcome.kind, TimedOut):
        return "timed_out"
    return "hard_provider_failure"


def _to_invocation_result(
    outcome: RuntimeOutcome, evidence_path: Path
) -> AgentRuntimeInvocationResult:
    if isinstance(outcome.kind, Completed):
        return AgentRuntimeInvocationResult(
            kind="completed",
            output=outcome.result.output,
            evidence_path=evidence_path,
        )
    if isinstance(outcome.kind, UsageLimited):
        return AgentRuntimeInvocationResult(
            kind="usage_limit",
            output=outcome.result.output,
            evidence_path=evidence_path,
            reset_time=outcome.kind.reset_time,
        )
    if isinstance(outcome.kind, ProviderUnavailable):
        if outcome.kind.reason == ProviderUnavailableReason.TRANSIENT_API_ERROR:
            return AgentRuntimeInvocationResult(
                kind="retryable_provider_failure",
                output="",
                evidence_path=evidence_path,
                message=outcome.kind.detail,
            )
        return AgentRuntimeInvocationResult(
            kind="hard_provider_failure",
            output="",
            evidence_path=evidence_path,
            message=outcome.kind.detail,
        )

    # Cancelled, TimedOut, and non-retryable provider failures become hard failures.
    detail = getattr(outcome.kind, "detail", "")
    message = outcome.result.output
    if not message and isinstance(detail, str):
        message = detail

    return AgentRuntimeInvocationResult(
        kind="hard_provider_failure",
        output=message or "",
        evidence_path=evidence_path,
        message=message or "",
    )


def invoke_agent_runtime(
    prompt: str,
    *,
    logs_root: Path,
    call_site: AgentRuntimeCallSiteName,
    provider_auth: ProviderAuth | None = None,
) -> AgentRuntimeInvocationResult:
    evidence_path = _new_evidence_path(logs_root=logs_root, call_site=call_site)

    def _on_live_output(event: AgentEvent) -> None:
        try:
            _safe_append(evidence_path, f"{_render_event(event)}\n")
        except OSError:
            # Best-effort evidence: logging failures never block a valid outcome.
            pass

    try:
        _persist_prompt(evidence_path, prompt)
        _persist_events_header(evidence_path)
    except OSError:
        # Best-effort evidence: logging failures never block a valid outcome.
        pass

    with tempfile.TemporaryDirectory(prefix="agent-runtime-worktree-") as worktree:
        request = _build_request(
            prompt=prompt,
            invocation_dir=Path(worktree),
            provider_auth=provider_auth,
            on_live_output=_on_live_output,
        )
        try:
            # run_ephemeral is async; the pipeline is synchronous, so we drive it
            # with a local event loop.
            outcome = asyncio.run(RuntimeClient().run_ephemeral(request))
            run_result: RunResult = outcome.result
            try:
                _persist_result(
                    evidence_path,
                    selected=run_result.selected,
                    usage=run_result.usage,
                    kind_name=_result_section_kind(outcome),
                )
            except OSError:
                # Best-effort evidence: logging failures never block a valid outcome.
                pass

            return _to_invocation_result(outcome, evidence_path)
        except AgentRuntimeError as exc:
            return AgentRuntimeInvocationResult(
                kind="hard_provider_failure",
                output="",
                evidence_path=evidence_path,
                message=str(exc),
            )
