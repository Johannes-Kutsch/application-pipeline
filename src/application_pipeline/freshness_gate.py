from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from application_pipeline.parser_log import RunLog
from application_pipeline.run_metrics import RunMetrics


class _Stub(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def source(self) -> str: ...

    @property
    def company(self) -> str | None: ...

    @property
    def title(self) -> str | None: ...

    @property
    def location(self) -> str | None: ...


class _Position(Protocol):
    @property
    def stub(self) -> _Stub: ...

    @property
    def title(self) -> str: ...

    @property
    def posted_date(self) -> date | None: ...

    @property
    def deadline(self) -> date | None: ...


class _DedupStore(Protocol):
    def mark_expired(self, key: _Stub) -> None: ...


@dataclass(frozen=True)
class _FreshnessVerdict:
    passes: bool
    reason: Literal[
        "passed", "too_old", "deadline_passed", "too_old_and_deadline_passed"
    ]
    age_days: int | None


def _evaluate(
    position: _Position,
    anchored_today: date,
    max_listing_age_days: int,
) -> _FreshnessVerdict:
    age_days: int | None = None
    too_old = False
    deadline_passed = False

    if position.posted_date is not None:
        age_days = (anchored_today - position.posted_date).days
        too_old = age_days > max_listing_age_days

    if position.deadline is not None:
        deadline_passed = position.deadline <= anchored_today

    if too_old and deadline_passed:
        return _FreshnessVerdict(
            passes=False, reason="too_old_and_deadline_passed", age_days=age_days
        )
    if too_old:
        return _FreshnessVerdict(passes=False, reason="too_old", age_days=age_days)
    if deadline_passed:
        return _FreshnessVerdict(
            passes=False, reason="deadline_passed", age_days=age_days
        )
    return _FreshnessVerdict(passes=True, reason="passed", age_days=age_days)


class FreshnessGate:
    def __init__(
        self,
        *,
        anchored_today: date,
        max_listing_age_days: int,
        dedup: _DedupStore,
        metrics: RunMetrics,
        run_log: RunLog,
    ) -> None:
        self._anchored_today = anchored_today
        self._max_listing_age_days = max_listing_age_days
        self._dedup = dedup
        self._metrics = metrics
        self._run_log = run_log
        self._counts: dict[str, int] = {
            "passed": 0,
            "too_old": 0,
            "deadline_passed": 0,
            "too_old_and_deadline_passed": 0,
        }

    def admit(self, position: _Position) -> bool:
        verdict = _evaluate(position, self._anchored_today, self._max_listing_age_days)
        self._run_log.transcript(
            "pipeline_freshness",
            {
                "url": position.stub.url,
                "title": position.title,
                "source": position.stub.source,
                "posted_date": position.posted_date.isoformat()
                if position.posted_date is not None
                else None,
                "deadline": position.deadline.isoformat()
                if position.deadline is not None
                else None,
                "anchored_today": self._anchored_today.isoformat(),
                "age_days": verdict.age_days,
                "passes": verdict.passes,
                "reason": verdict.reason,
            },
        )
        self._counts[verdict.reason] += 1
        if not verdict.passes:
            self._dedup.mark_expired(position.stub)
            self._metrics.freshness_dropped()
        return verdict.passes

    def emit_run_complete(self) -> None:
        self._run_log.event("pipeline_freshness", "run_complete", **self._counts)
