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
    posted_date: date | None = None


@pytest.fixture
def logs_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def run_log(logs_dir: Path) -> RunLog:
    return RunLog(logs_dir)


@pytest.fixture
def display() -> FakeStatusDisplay:
    return FakeStatusDisplay()


@pytest.fixture
def dedup(tmp_path: Path):
    return dedup_load(tmp_path / ".seen.json")


ANCHORED_TODAY = date(2026, 1, 15)
MAX_AGE = 30


def _make_gate(
    tmp_path: Path,
    run_log: RunLog,
    display: FakeStatusDisplay,
    dedup,
    anchored_today: date = ANCHORED_TODAY,
    max_listing_age_days: int = MAX_AGE,
) -> FreshnessGate:
    return FreshnessGate(
        anchored_today=anchored_today,
        max_listing_age_days=max_listing_age_days,
        dedup=dedup,
        display=display,
        run_log=run_log,
    )


def _read_transcripts(logs_dir: Path) -> list[dict]:
    path = logs_dir / "pipeline" / "freshness.transcripts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_events(logs_dir: Path) -> list[dict]:
    path = logs_dir / "pipeline" / "freshness.events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# admit() return value + transcript
# ---------------------------------------------------------------------------


def test_admit_no_dates_returns_true(
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/1")
    assert gate.admit(stub, gate_arm="post_llm") is True


def test_admit_writes_transcript_row_for_passing_position(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(
        url="https://example.com/job1",
        title="Engineer",
        source="my-source",
        posted_date=date(2026, 1, 10),
    )
    gate.admit(stub, gate_arm="post_llm", deadline=date(2026, 2, 1))
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
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    # posted_date exactly max_listing_age_days ago should pass (age == 30, not > 30)
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(
        url="https://example.com/1", posted_date=date(2025, 12, 16)
    )  # 30 days before 2026-01-15
    assert gate.admit(stub, gate_arm="post_llm") is True


def test_admit_one_day_over_threshold_returns_false_with_too_old_reason(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(
        url="https://example.com/old", posted_date=date(2025, 12, 15)
    )  # 31 days before 2026-01-15
    result = gate.admit(stub, gate_arm="post_llm")
    assert result is False
    rows = _read_transcripts(logs_dir)
    assert rows[0]["reason"] == "too_old"
    assert rows[0]["passes"] is False
    assert rows[0]["age_days"] == 31


def test_admit_future_posted_date_passes_with_negative_age_days(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(
        url="https://example.com/1", posted_date=date(2026, 1, 20)
    )  # 5 days in the future
    assert gate.admit(stub, gate_arm="post_llm") is True
    rows = _read_transcripts(logs_dir)
    assert rows[0]["age_days"] == -5


def test_admit_deadline_today_returns_false_with_deadline_passed_reason(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/deadline-today")
    assert gate.admit(stub, gate_arm="post_llm", deadline=ANCHORED_TODAY) is False
    rows = _read_transcripts(logs_dir)
    assert rows[0]["reason"] == "deadline_passed"


def test_admit_deadline_tomorrow_passes(
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/1")
    assert gate.admit(stub, gate_arm="post_llm", deadline=date(2026, 1, 16)) is True


def test_admit_deadline_yesterday_returns_false(
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/1")
    assert gate.admit(stub, gate_arm="post_llm", deadline=date(2026, 1, 14)) is False


def test_admit_combined_too_old_and_deadline_passed(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(
        url="https://example.com/both", posted_date=date(2025, 12, 15)
    )  # 31 days ago
    assert gate.admit(stub, gate_arm="post_llm", deadline=ANCHORED_TODAY) is False
    rows = _read_transcripts(logs_dir)
    assert rows[0]["reason"] == "too_old_and_deadline_passed"


def test_admit_both_none_is_silent_noop_no_transcript(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/1")
    result = gate.admit(stub, gate_arm="post_llm")
    assert result is True
    assert _read_transcripts(logs_dir) == []


def test_admit_both_none_does_not_increment_passed_counter(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/1")
    gate.admit(stub, gate_arm="post_llm")
    gate.emit_run_complete()
    events = _read_events(logs_dir)
    ev = events[0]
    assert ev["passed"] == 0


def test_admit_both_none_does_not_mark_expired(
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/noop")
    gate.admit(stub, gate_arm="post_llm")
    assert dedup.is_seen(stub).kind == "miss"


# ---------------------------------------------------------------------------
# admit() drop side-effects: dedup + display
# ---------------------------------------------------------------------------


def test_admit_drop_marks_expired_in_dedup_store(
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/old", posted_date=date(2025, 12, 15))
    gate.admit(stub, gate_arm="post_llm")
    assert dedup.is_seen(stub).kind == "url_hit"
    # Verify expired status in the persisted JSON
    seen_path = tmp_path / ".seen.json"
    data = json.loads(seen_path.read_text())
    record = next(
        r for r in data.values() if "https://example.com/old" in r.get("urls", [])
    )
    assert record["status"] == "expired"


def test_admit_drop_does_not_publish_to_pipeline_freshness_row(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, dedup
) -> None:
    """pipeline_freshness status row is retired; drops no longer publish to it."""
    display = FakeStatusDisplay()

    gate = FreshnessGate(
        anchored_today=ANCHORED_TODAY,
        max_listing_age_days=MAX_AGE,
        dedup=dedup,
        display=display,
        run_log=run_log,
    )

    gate.admit(
        _Stub(url="https://example.com/a", posted_date=date(2025, 12, 15)),
        gate_arm="post_llm",
    )
    gate.admit(
        _Stub(url="https://example.com/c"), gate_arm="post_llm"
    )  # passes (both None)

    assert display.body_updates_for("pipeline_freshness") == []


# ---------------------------------------------------------------------------
# emit_run_complete()
# ---------------------------------------------------------------------------


def test_emit_run_complete_writes_event_row_with_per_reason_counts(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)

    # passed (fresh posted_date within threshold)
    gate.admit(
        _Stub(url="https://example.com/p1", posted_date=date(2026, 1, 10)),
        gate_arm="post_llm",
    )
    gate.admit(
        _Stub(url="https://example.com/p2", posted_date=date(2026, 1, 12)),
        gate_arm="post_llm",
    )
    # too_old
    gate.admit(
        _Stub(url="https://example.com/o1", posted_date=date(2025, 12, 15)),
        gate_arm="post_llm",
    )
    # deadline_passed
    gate.admit(
        _Stub(url="https://example.com/d1"),
        gate_arm="post_llm",
        deadline=ANCHORED_TODAY,
    )
    # too_old_and_deadline_passed
    gate.admit(
        _Stub(url="https://example.com/b1", posted_date=date(2025, 12, 15)),
        gate_arm="post_llm",
        deadline=ANCHORED_TODAY,
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


# ---------------------------------------------------------------------------
# admit() — pre-LLM (discover) arm
# ---------------------------------------------------------------------------


def test_admit_stub_with_stale_posted_date_returns_false(
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(
        url="https://example.com/old", posted_date=date(2025, 12, 15)
    )  # 31 days ago
    assert gate.admit(stub, gate_arm="discover") is False


def test_admit_stub_with_null_posted_date_returns_true(
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/no-date", posted_date=None)
    assert gate.admit(stub, gate_arm="discover") is True


def test_admit_stub_with_fresh_posted_date_returns_true(
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(
        url="https://example.com/fresh", posted_date=date(2026, 1, 10)
    )  # 5 days ago
    assert gate.admit(stub, gate_arm="discover") is True


def test_admit_stub_at_age_cutoff_returns_true(
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(
        url="https://example.com/cutoff", posted_date=date(2025, 12, 16)
    )  # exactly 30 days ago
    assert gate.admit(stub, gate_arm="discover") is True


def test_admit_stub_stale_writes_transcript_with_discover_arm(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/old", posted_date=date(2025, 12, 15))
    gate.admit(stub, gate_arm="discover")
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["gate_arm"] == "discover"
    assert row["url"] == "https://example.com/old"
    assert row["passes"] is False
    assert row["reason"] == "too_old"
    assert row["deadline"] is None


def test_admit_stub_null_posted_date_writes_no_transcript(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/no-date", posted_date=None)
    gate.admit(stub, gate_arm="discover")
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 0


def test_admit_stub_stale_marks_expired_in_dedup(
    tmp_path: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/old", posted_date=date(2025, 12, 15))
    gate.admit(stub, gate_arm="discover")
    assert dedup.is_seen(stub).kind == "url_hit"


def test_admit_post_enrich_null_posted_date_is_silent_noop(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/no-date", posted_date=None)
    assert gate.admit(stub, gate_arm="post_enrich") is True
    assert _read_transcripts(logs_dir) == []


def test_admit_writes_transcript_with_post_llm_arm(
    tmp_path: Path, logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay, dedup
) -> None:
    gate = _make_gate(tmp_path, run_log, display, dedup)
    stub = _Stub(url="https://example.com/job1", posted_date=date(2026, 1, 10))
    gate.admit(stub, gate_arm="post_llm")
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 1
    assert rows[0]["gate_arm"] == "post_llm"
