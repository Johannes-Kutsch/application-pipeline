from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from application_pipeline.prefilter.freshness import evaluate

TODAY = date(2025, 6, 15)
MAX_AGE = 180


@dataclass
class StubPosition:
    posted_date: date | None
    deadline: date | None


def test_no_dates_passes() -> None:
    pos = StubPosition(posted_date=None, deadline=None)
    verdict = evaluate(pos, anchored_today=TODAY, max_listing_age_days=MAX_AGE)
    assert verdict.passes is True
    assert verdict.reason == "passed"
    assert verdict.age_days is None


def test_posted_date_at_threshold_passes() -> None:
    pos = StubPosition(posted_date=TODAY - timedelta(days=MAX_AGE), deadline=None)
    verdict = evaluate(pos, anchored_today=TODAY, max_listing_age_days=MAX_AGE)
    assert verdict.passes is True


def test_posted_date_one_over_threshold_fails() -> None:
    pos = StubPosition(posted_date=TODAY - timedelta(days=MAX_AGE + 1), deadline=None)
    verdict = evaluate(pos, anchored_today=TODAY, max_listing_age_days=MAX_AGE)
    assert verdict.passes is False
    assert verdict.reason == "too_old"


def test_deadline_yesterday_fails() -> None:
    pos = StubPosition(posted_date=None, deadline=TODAY - timedelta(days=1))
    verdict = evaluate(pos, anchored_today=TODAY, max_listing_age_days=MAX_AGE)
    assert verdict.passes is False
    assert verdict.reason == "deadline_passed"


def test_deadline_today_fails() -> None:
    pos = StubPosition(posted_date=None, deadline=TODAY)
    verdict = evaluate(pos, anchored_today=TODAY, max_listing_age_days=MAX_AGE)
    assert verdict.passes is False
    assert verdict.reason == "deadline_passed"


def test_deadline_tomorrow_passes() -> None:
    pos = StubPosition(posted_date=None, deadline=TODAY + timedelta(days=1))
    verdict = evaluate(pos, anchored_today=TODAY, max_listing_age_days=MAX_AGE)
    assert verdict.passes is True


def test_both_arms_violated_returns_combined_reason() -> None:
    pos = StubPosition(
        posted_date=TODAY - timedelta(days=MAX_AGE + 1),
        deadline=TODAY - timedelta(days=1),
    )
    verdict = evaluate(pos, anchored_today=TODAY, max_listing_age_days=MAX_AGE)
    assert verdict.passes is False
    assert verdict.reason == "too_old_and_deadline_passed"


def test_future_posted_date_passes_with_negative_age() -> None:
    pos = StubPosition(posted_date=TODAY + timedelta(days=5), deadline=None)
    verdict = evaluate(pos, anchored_today=TODAY, max_listing_age_days=MAX_AGE)
    assert verdict.passes is True
    assert verdict.reason == "passed"
    assert verdict.age_days == -5


def test_verdict_is_frozen() -> None:
    pos = StubPosition(posted_date=None, deadline=None)
    verdict = evaluate(pos, anchored_today=TODAY, max_listing_age_days=MAX_AGE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.passes = False  # type: ignore[misc]


def test_age_days_recorded_when_posted_date_present() -> None:
    pos = StubPosition(posted_date=TODAY - timedelta(days=10), deadline=None)
    verdict = evaluate(pos, anchored_today=TODAY, max_listing_age_days=MAX_AGE)
    assert verdict.age_days == 10
