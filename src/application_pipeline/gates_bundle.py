"""Gates Bundle — groups all non-LLM gates behind a single call site.

Pre-enrich invocation: Dedup (Run-scoped + Store), Domain Pre-Filter,
Freshness Gate (gate_arm="discover"). Content Gate no-ops (no body yet).
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from application_pipeline.dedup.store import RunScopedSeenResult
from application_pipeline.parser_log import RunLog
from application_pipeline.run_metrics import RunMetrics

Verdict = Literal["pass", "drop", "judge_pending"]


class _Stub(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def title(self) -> str | None: ...

    @property
    def source(self) -> str: ...

    @property
    def company(self) -> str | None: ...

    @property
    def location(self) -> str | None: ...


class _DedupLike(Protocol):
    def is_seen(self, key: Any) -> RunScopedSeenResult: ...


class _PreFilterLike(Protocol):
    def admit_stub(self, stub: Any) -> bool: ...


class _FreshnessLike(Protocol):
    def admit_stub(
        self,
        stub: Any,
        *,
        gate_arm: Literal["discover", "post_enrich"] = "discover",
    ) -> bool: ...


class _ContentLike(Protocol):
    def admit(self, stripped_body: str, stub: Any) -> bool: ...


class _DedupCountersLike(Protocol):
    def record(self, result: RunScopedSeenResult) -> None: ...


def run_gates(
    stub: _Stub,
    *,
    run_log: RunLog,
    metrics: RunMetrics,
    dedup_counters: _DedupCountersLike,
    dedup: _DedupLike,
    prefilter: _PreFilterLike,
    freshness: _FreshnessLike,
    content: _ContentLike | None = None,
    gate_arm: Literal["discover", "post_enrich"] = "discover",
    body: str | None = None,
) -> Verdict:
    """Evaluate all non-LLM gates for a stub; return the first drop or pass.

    Pre-enrich: Content Gate no-ops (body is None).
    Post-enrich: dedup "run_hit" is treated as pass — the stub already entered
    the in-run set during the pre-enrich invocation and is being processed normally.
    Each gate owns its own transcript writes.
    """
    if not freshness.admit_stub(stub, gate_arm=gate_arm):
        return "drop"

    if gate_arm != "post_enrich":
        result = dedup.is_seen(stub)
        dedup_counters.record(result)
        if result == "judge_pending":
            return "judge_pending"
        if result != "miss":
            return "drop"

    if not prefilter.admit_stub(stub):
        return "drop"

    if gate_arm == "post_enrich" and body is not None and content is not None:
        if not content.admit(body, stub):
            return "drop"

    return "pass"
