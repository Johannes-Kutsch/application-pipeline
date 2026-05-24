from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from application_pipeline.parser_log import RunLog
from application_pipeline.status_display import StatusDisplay


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

    @property
    def posted_date(self) -> date | None: ...


class _DedupStore(Protocol):
    def mark_expired(self, key: _Stub) -> None: ...


@dataclass(frozen=True)
class _FreshnessVerdict:
    passes: bool
    reason: Literal[
        "passed", "too_old", "deadline_passed", "too_old_and_deadline_passed"
    ]
    age_days: int | None


@dataclass(frozen=True)
class FreshnessSnapshot:
    freshness_dropped: int = 0


def _evaluate_freshness(
    posted_date: date | None,
    deadline: date | None,
    anchored_today: date,
    max_listing_age_days: int,
) -> _FreshnessVerdict | None:
    """Returns None when both dates are absent (silent no-op signal)."""
    if posted_date is None and deadline is None:
        return None
    age_days: int | None = None
    too_old = False
    deadline_passed = False

    if posted_date is not None:
        age_days = (anchored_today - posted_date).days
        too_old = age_days > max_listing_age_days

    if deadline is not None:
        deadline_passed = deadline <= anchored_today

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
        display: StatusDisplay,
        run_log: RunLog,
    ) -> None:
        self._anchored_today = anchored_today
        self._max_listing_age_days = max_listing_age_days
        self._dedup = dedup
        self._display = display
        self._run_log = run_log
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {
            "passed": 0,
            "too_old": 0,
            "deadline_passed": 0,
            "too_old_and_deadline_passed": 0,
        }

    def admit(
        self,
        stub: _Stub,
        *,
        gate_arm: Literal["discover", "post_enrich", "post_llm"],
        deadline: date | None = None,
    ) -> bool:
        verdict = _evaluate_freshness(
            stub.posted_date, deadline, self._anchored_today, self._max_listing_age_days
        )
        if verdict is None:
            return True
        self._run_log.transcript(
            "pipeline_freshness",
            {
                "url": stub.url,
                "title": stub.title,
                "source": stub.source,
                "posted_date": stub.posted_date.isoformat()
                if stub.posted_date is not None
                else None,
                "deadline": deadline.isoformat() if deadline is not None else None,
                "anchored_today": self._anchored_today.isoformat(),
                "age_days": verdict.age_days,
                "passes": verdict.passes,
                "reason": verdict.reason,
                "gate_arm": gate_arm,
            },
        )
        with self._lock:
            self._counts[verdict.reason] += 1
        if not verdict.passes:
            self._dedup.mark_expired(stub)
        return verdict.passes

    def snapshot(self) -> FreshnessSnapshot:
        with self._lock:
            return FreshnessSnapshot(freshness_dropped=self._dropped_count())

    def emit_run_complete(self) -> None:
        self._run_log.event("pipeline_freshness", "run_complete", **self._counts)

    def _dropped_count(self) -> int:
        return (
            self._counts["too_old"]
            + self._counts["deadline_passed"]
            + self._counts["too_old_and_deadline_passed"]
        )
