from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline.dedup import load as dedup_load
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.parser_log import RunLog
from application_pipeline.run_metrics import RunMetrics


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@dataclass
class _Stub:
    url: str
    source: str = "test-source"
    title: str | None = "Test Job"
    company: str | None = "Acme"
    location: str | None = "Remote"


@dataclass
class _Position:
    stub: _Stub
    title: str = "Test Job"
    posted_date: date | None = None
    deadline: date | None = None


def _make_position(
    url: str = "https://example.com/1",
    title: str = "Test Job",
    posted_date: date | None = None,
    deadline: date | None = None,
    source: str = "test-source",
) -> _Position:
    return _Position(
        stub=_Stub(url=url, source=source, title=title),
        title=title,
        posted_date=posted_date,
        deadline=deadline,
    )


@pytest.fixture
def logs_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def run_log(logs_dir: Path) -> RunLog:
    return RunLog(logs_dir)


@pytest.fixture
def metrics(tmp_path: Path, run_log: RunLog) -> RunMetrics:
    display = FakeStatusDisplay()
    m = RunMetrics(display, run_log=run_log)
    m.register_rows(starting_order=0)
    return m


@pytest.fixture
def dedup(tmp_path: Path):
    return dedup_load(tmp_path / ".seen.json")


ANCHORED_TODAY = date(2026, 1, 15)
MAX_AGE = 30


def _make_gate(
    tmp_path: Path,
    run_log: RunLog,
    metrics: RunMetrics,
    dedup,
    anchored_today: date = ANCHORED_TODAY,
    max_listing_age_days: int = MAX_AGE,
) -> FreshnessGate:
    return FreshnessGate(
        anchored_today=anchored_today,
        max_listing_age_days=max_listing_age_days,
        dedup=dedup,
        metrics=metrics,
        run_log=run_log,
    )


def _read_transcripts(logs_dir: Path) -> list[dict]:
    path = logs_dir / "pipeline_freshness.transcripts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_events(logs_dir: Path) -> list[dict]:
    path = logs_dir / "pipeline_freshness.events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# admit() return value + transcript
# ---------------------------------------------------------------------------


def test_admit_no_dates_returns_true(
    tmp_path: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    position = _make_position(url="https://example.com/1")
    assert gate.admit(position) is True


def test_admit_writes_transcript_row_for_passing_position(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    position = _make_position(
        url="https://example.com/job1",
        title="Engineer",
        posted_date=date(2026, 1, 10),
        deadline=date(2026, 2, 1),
        source="my-source",
    )
    gate.admit(position)
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://example.com/job1"
    assert row["title"] == "Engineer"
    assert row["source"] == "my-source"
    assert row["posted_date"] == "2026-01-10"
    assert row["deadline"] == "2026-02-01"
    assert row["anchored_today"] == "2026-01-15"
    assert row["age_days"] == 5
    assert row["passes"] is True
    assert row["reason"] == "passed"


def test_admit_at_threshold_posted_date_passes(
    tmp_path: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    # posted_date exactly max_listing_age_days ago should pass (age == 30, not > 30)
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    position = _make_position(
        posted_date=date(2025, 12, 16)
    )  # 30 days before 2026-01-15
    assert gate.admit(position) is True


def test_admit_one_day_over_threshold_returns_false_with_too_old_reason(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    position = _make_position(
        url="https://example.com/old",
        posted_date=date(2025, 12, 15),  # 31 days before 2026-01-15
    )
    result = gate.admit(position)
    assert result is False
    rows = _read_transcripts(logs_dir)
    assert rows[0]["reason"] == "too_old"
    assert rows[0]["passes"] is False
    assert rows[0]["age_days"] == 31


def test_admit_future_posted_date_passes_with_negative_age_days(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    position = _make_position(posted_date=date(2026, 1, 20))  # 5 days in the future
    assert gate.admit(position) is True
    rows = _read_transcripts(logs_dir)
    assert rows[0]["age_days"] == -5


def test_admit_deadline_today_returns_false_with_deadline_passed_reason(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    position = _make_position(
        url="https://example.com/deadline-today",
        deadline=ANCHORED_TODAY,
    )
    assert gate.admit(position) is False
    rows = _read_transcripts(logs_dir)
    assert rows[0]["reason"] == "deadline_passed"


def test_admit_deadline_tomorrow_passes(
    tmp_path: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    position = _make_position(deadline=date(2026, 1, 16))
    assert gate.admit(position) is True


def test_admit_deadline_yesterday_returns_false(
    tmp_path: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    position = _make_position(deadline=date(2026, 1, 14))
    assert gate.admit(position) is False


def test_admit_combined_too_old_and_deadline_passed(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    position = _make_position(
        url="https://example.com/both",
        posted_date=date(2025, 12, 15),  # 31 days ago
        deadline=ANCHORED_TODAY,
    )
    assert gate.admit(position) is False
    rows = _read_transcripts(logs_dir)
    assert rows[0]["reason"] == "too_old_and_deadline_passed"


def test_admit_null_dates_transcript_fields_are_none(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    gate.admit(_make_position())
    row = _read_transcripts(logs_dir)[0]
    assert row["posted_date"] is None
    assert row["deadline"] is None
    assert row["age_days"] is None


# ---------------------------------------------------------------------------
# admit() drop side-effects: dedup + metrics
# ---------------------------------------------------------------------------


def test_admit_drop_marks_expired_in_dedup_store(
    tmp_path: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)
    position = _make_position(
        url="https://example.com/old",
        posted_date=date(2025, 12, 15),
    )
    gate.admit(position)
    assert dedup.is_seen(position.stub) == "url_hit"
    # Verify expired status in the persisted JSON
    seen_path = tmp_path / ".seen.json"
    data = json.loads(seen_path.read_text())
    assert data["https://example.com/old"]["status"] == "expired"


def test_admit_drop_in_domain_transition_removes_extract(
    tmp_path: Path, run_log: RunLog, metrics: RunMetrics
) -> None:
    from application_pipeline.extracts import load as extract_load
    from application_pipeline.llm.types import StructuredExtract

    extract_store = extract_load(tmp_path / "extracts.json")
    dedup = dedup_load(tmp_path / ".seen.json", extract_store=extract_store)

    url = "https://example.com/in-domain"

    @dataclass
    class _S:
        url: str
        company: str = "Acme"
        title: str = "Engineer"
        location: str = "Remote"

    stub = _S(url=url)
    extract = StructuredExtract(
        seniority="senior",
        work_model="remote",
        contract_type="permanent",
        key_skills=["python"],
        key_responsibilities=["ship things"],
        must_have_requirements=["3+ years"],
        notable_caveats="",
    )
    dedup.mark_in_domain(stub, extract=extract)
    assert extract_store.get(url) is not None

    gate = FreshnessGate(
        anchored_today=ANCHORED_TODAY,
        max_listing_age_days=MAX_AGE,
        dedup=dedup,
        metrics=metrics,
        run_log=RunLog(tmp_path / "logs2"),
    )
    position = _make_position(url=url, posted_date=date(2025, 12, 15))
    gate.admit(position)

    assert extract_store.get(url) is None


def test_admit_drop_increments_freshness_dropped_metric(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, dedup
) -> None:
    display = FakeStatusDisplay()
    m = RunMetrics(display, run_log=run_log)
    m.register_rows(starting_order=0)

    gate = FreshnessGate(
        anchored_today=ANCHORED_TODAY,
        max_listing_age_days=MAX_AGE,
        dedup=dedup,
        metrics=m,
        run_log=run_log,
    )

    gate.admit(
        _make_position(url="https://example.com/a", posted_date=date(2025, 12, 15))
    )
    gate.admit(
        _make_position(url="https://example.com/b", posted_date=date(2025, 12, 14))
    )
    gate.admit(_make_position(url="https://example.com/c"))  # passes

    # RunSummary doesn't expose freshness_dropped directly; check display body updates
    freshness_bodies = display.body_updates_for("pipeline_freshness")
    assert len(freshness_bodies) == 2  # one update per drop


# ---------------------------------------------------------------------------
# emit_run_complete()
# ---------------------------------------------------------------------------


def test_emit_run_complete_writes_event_row_with_per_reason_counts(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, metrics, dedup)

    # passed
    gate.admit(_make_position(url="https://example.com/p1"))
    gate.admit(_make_position(url="https://example.com/p2"))
    # too_old
    gate.admit(
        _make_position(url="https://example.com/o1", posted_date=date(2025, 12, 15))
    )
    # deadline_passed
    gate.admit(_make_position(url="https://example.com/d1", deadline=ANCHORED_TODAY))
    # too_old_and_deadline_passed
    gate.admit(
        _make_position(
            url="https://example.com/b1",
            posted_date=date(2025, 12, 15),
            deadline=ANCHORED_TODAY,
        )
    )

    gate.emit_run_complete()

    events = _read_events(logs_dir)
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "run_complete"
    assert ev["passed"] == 2
    assert ev["too_old"] == 1
    assert ev["deadline_passed"] == 1
    assert ev["too_old_and_deadline_passed"] == 1
