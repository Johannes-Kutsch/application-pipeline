from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol


class _Position(Protocol):
    @property
    def posted_date(self) -> date | None: ...

    @property
    def deadline(self) -> date | None: ...


@dataclass(frozen=True)
class FreshnessVerdict:
    passes: bool
    reason: Literal[
        "passed", "too_old", "deadline_passed", "too_old_and_deadline_passed"
    ]
    age_days: int | None


def evaluate(
    position: _Position,
    anchored_today: date,
    max_listing_age_days: int,
) -> FreshnessVerdict:
    age_days: int | None = None
    too_old = False
    deadline_passed = False

    if position.posted_date is not None:
        age_days = (anchored_today - position.posted_date).days
        too_old = age_days > max_listing_age_days

    if position.deadline is not None:
        deadline_passed = position.deadline <= anchored_today

    if too_old and deadline_passed:
        return FreshnessVerdict(
            passes=False, reason="too_old_and_deadline_passed", age_days=age_days
        )
    if too_old:
        return FreshnessVerdict(passes=False, reason="too_old", age_days=age_days)
    if deadline_passed:
        return FreshnessVerdict(
            passes=False, reason="deadline_passed", age_days=age_days
        )
    return FreshnessVerdict(passes=True, reason="passed", age_days=age_days)
