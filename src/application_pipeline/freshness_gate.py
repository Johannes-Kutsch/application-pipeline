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


@dataclass(frozen=True)
class FreshnessSnapshot:
    freshness_dropped: int = 0


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


def _evaluate_stub(
    stub: _Stub,
    anchored_today: date,
    max_listing_age_days: int,
) -> _FreshnessVerdict | None:
    """Evaluate freshness from stub posted_date only. Returns None if posted_date is absent."""
    if stub.posted_date is None:
        return None
    age_days = (anchored_today - stub.posted_date).days
    if age_days > max_listing_age_days:
        return _FreshnessVerdict(passes=False, reason="too_old", age_days=age_days)
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

    def admit_stub(
        self,
        stub: _Stub,
        *,
        gate_arm: Literal["discover", "post_enrich"] = "discover",
    ) -> bool:
        """Stub arm: evaluate freshness from stub posted_date.

        Returns True (pass through) when posted_date is absent. Returns False and
        drops the stub when it is stale. gate_arm selects the transcript label.
        """
        verdict = _evaluate_stub(stub, self._anchored_today, self._max_listing_age_days)
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
                "deadline": None,
                "anchored_today": self._anchored_today.isoformat(),
                "age_days": verdict.age_days,
                "passes": verdict.passes,
                "reason": verdict.reason,
                "gate_arm": gate_arm,
            },
        )
        with self._lock:
            self._counts[verdict.reason] += 1
            body = self._freshness_body()
        if not verdict.passes:
            self._dedup.mark_expired(stub)
            self._display.update_body("pipeline_freshness", body=body)
        return verdict.passes

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
                "gate_arm": "post_llm",
            },
        )
        with self._lock:
            self._counts[verdict.reason] += 1
            body = self._freshness_body()
        if not verdict.passes:
            self._dedup.mark_expired(position.stub)
            self._display.update_body("pipeline_freshness", body=body)
        return verdict.passes

    def snapshot(self) -> FreshnessSnapshot:
        with self._lock:
            dropped = (
                self._counts["too_old"]
                + self._counts["deadline_passed"]
                + self._counts["too_old_and_deadline_passed"]
            )
        return FreshnessSnapshot(freshness_dropped=dropped)

    def emit_run_complete(self) -> None:
        self._run_log.event("pipeline_freshness", "run_complete", **self._counts)

    def _freshness_body(self) -> str:
        dropped = (
            self._counts["too_old"]
            + self._counts["deadline_passed"]
            + self._counts["too_old_and_deadline_passed"]
        )
        return f"dropped={dropped}"
