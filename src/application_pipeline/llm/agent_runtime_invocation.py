from __future__ import annotations

import asyncio
import tempfile
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal
from typing import Protocol

from agent_runtime import (
    AgentRuntimeError,
    RuntimeClient,
    RuntimeOutcome,
)
from agent_runtime.contracts import ToolAccess
from agent_runtime.runtime import (
    AgentEvent,
    EphemeralRunRequest,
    InvocationRecord,
    ProviderAuth,
)
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
    evidence_dir: Path
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


def _new_evidence_dir(logs_root: Path, call_site: AgentRuntimeCallSiteName) -> Path:
    parent = _evidence_parent(logs_root, call_site)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S-%f")
    return parent / f"llm-{call_site}-{stamp}"


def _build_request(
    prompt: str, invocation_dir: Path, provider_auth: ProviderAuth | None
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
    )


def _render_events(events: tuple[AgentEvent, ...]) -> str:
    lines: list[str] = []
    for event in events:
        parts: list[str] = [event.type]
        if event.tool_name:
            parts.append(f"tool={event.tool_name}")
        body = event.text or event.payload or event.raw_provider_output
        if body:
            parts.append(body)
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _render_meta(record: InvocationRecord, outcome_kind: str) -> str:
    lines = [
        f"outcome: {record.outcome or outcome_kind}",
        f"provider_session_id: {record.provider_session_id or ''}",
    ]
    if record.usage is not None:
        lines.append(f"usage: {record.usage}")
    return "\n".join(lines) + "\n"


def _decode(provider_output: bytes | None) -> str:
    if provider_output is None:
        return ""
    return provider_output.decode("utf-8", errors="replace")


def _completed_output(outcome: RuntimeOutcome) -> str:
    if outcome.output:
        return outcome.output
    records = outcome.invocation_records or ()
    for record in reversed(records):
        decoded = _decode(record.provider_output)
        if decoded:
            return decoded
    return outcome.output


def _suffix(index: int) -> str:
    return "" if index == 0 else f".{index}"


def _write_evidence(
    *,
    evidence_dir: Path,
    prompt: str,
    outcome: RuntimeOutcome,
) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "prompt").write_text(prompt, encoding="utf-8")
    records = outcome.invocation_records or ()
    if not records:
        # Diagnostic gap: a completed call produced no provider evidence.
        (evidence_dir / "response").write_text("", encoding="utf-8")
        (evidence_dir / "events").write_text("", encoding="utf-8")
        (evidence_dir / "meta").write_text(
            f"outcome: {outcome.kind}\nprovider_session_id: \n", encoding="utf-8"
        )
        return
    for index, record in enumerate(records):
        suffix = _suffix(index)
        (evidence_dir / f"response{suffix}").write_text(
            _decode(record.provider_output), encoding="utf-8"
        )
        (evidence_dir / f"events{suffix}").write_text(
            _render_events(record.events), encoding="utf-8"
        )
        (evidence_dir / f"meta{suffix}").write_text(
            _render_meta(record, outcome.kind), encoding="utf-8"
        )


def _persist_evidence(
    *,
    logs_root: Path,
    call_site: AgentRuntimeCallSiteName,
    prompt: str,
    outcome: RuntimeOutcome,
) -> Path:
    evidence_dir = _new_evidence_dir(logs_root, call_site)
    try:
        _write_evidence(evidence_dir=evidence_dir, prompt=prompt, outcome=outcome)
    except OSError:
        # Best-effort: a logging/persist error must never break the run.
        pass
    return evidence_dir


def _result_from_outcome(
    outcome: RuntimeOutcome, evidence_dir: Path
) -> AgentRuntimeInvocationResult:
    if outcome.kind == "completed":
        return AgentRuntimeInvocationResult(
            kind="completed",
            output=_completed_output(outcome),
            evidence_dir=evidence_dir,
        )
    elif outcome.kind == "usage_limited":
        return AgentRuntimeInvocationResult(
            kind="usage_limit",
            output=outcome.output,
            evidence_dir=evidence_dir,
            reset_time=outcome.reset_time,
        )
    elif outcome.kind == "retryable_provider_failure":
        kind: AgentRuntimeInvocationKind = "retryable_provider_failure"
    else:
        kind = "hard_provider_failure"
    return AgentRuntimeInvocationResult(
        kind=kind,
        output=outcome.output,
        evidence_dir=evidence_dir,
    )


def invoke_agent_runtime(
    prompt: str,
    *,
    logs_root: Path,
    call_site: AgentRuntimeCallSiteName,
    provider_auth: ProviderAuth | None = None,
) -> AgentRuntimeInvocationResult:
    with tempfile.TemporaryDirectory(prefix="agent-runtime-worktree-") as worktree:
        request = _build_request(
            prompt=prompt,
            invocation_dir=Path(worktree),
            provider_auth=provider_auth,
        )
        try:
            # run_ephemeral is async (agent_runtime 0.0.2); the pipeline is
            # synchronous (classify worker threads, single judge call), so we
            # drive the coroutine to completion on a per-call event loop.
            outcome = asyncio.run(RuntimeClient().run_ephemeral(request))
        except AgentRuntimeError as exc:
            evidence_dir = _new_evidence_dir(logs_root, call_site)
            return AgentRuntimeInvocationResult(
                kind="hard_provider_failure",
                output="",
                evidence_dir=evidence_dir,
                message=str(exc),
            )
        evidence_dir = _persist_evidence(
            logs_root=logs_root,
            call_site=call_site,
            prompt=prompt,
            outcome=outcome,
        )
        return _result_from_outcome(outcome, evidence_dir=evidence_dir)
