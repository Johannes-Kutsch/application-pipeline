from __future__ import annotations

import json
from pathlib import Path

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline.dedup_counters import DedupCounters, DedupSnapshot
from application_pipeline.parser_log import RunLog


@pytest.fixture
def logs_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def run_log(logs_dir: Path) -> RunLog:
    return RunLog(logs_dir)


@pytest.fixture
def display() -> FakeStatusDisplay:
    return FakeStatusDisplay()


def _make(run_log: RunLog, display: FakeStatusDisplay) -> DedupCounters:
    return DedupCounters(display=display, run_log=run_log)


def _read_events(logs_dir: Path) -> list[dict]:
    path = logs_dir / "pipeline" / "dedup.events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Per-variant accumulation
# ---------------------------------------------------------------------------


def test_record_url_hit_increments_url_hits(
    run_log: RunLog, display: FakeStatusDisplay
) -> None:
    counters = _make(run_log, display)
    counters.record("url_hit")
    counters.record("url_hit")
    snap = counters.snapshot()
    assert snap.dedup_url_hits == 2
    assert snap.dedup_tuple_hits == 0
    assert snap.dedup_run_hits == 0
    assert snap.dedup_misses == 0
    assert snap.judge_resumed == 0


def test_record_tuple_hit_increments_tuple_hits(
    run_log: RunLog, display: FakeStatusDisplay
) -> None:
    counters = _make(run_log, display)
    counters.record("tuple_hit")
    snap = counters.snapshot()
    assert snap.dedup_tuple_hits == 1


def test_record_run_hit_increments_run_hits(
    run_log: RunLog, display: FakeStatusDisplay
) -> None:
    counters = _make(run_log, display)
    counters.record("run_hit")
    snap = counters.snapshot()
    assert snap.dedup_run_hits == 1


def test_record_miss_increments_misses(
    run_log: RunLog, display: FakeStatusDisplay
) -> None:
    counters = _make(run_log, display)
    counters.record("miss")
    snap = counters.snapshot()
    assert snap.dedup_misses == 1


def test_record_fuzzy_hit_increments_fuzzy_hits(
    run_log: RunLog, display: FakeStatusDisplay
) -> None:
    counters = _make(run_log, display)
    counters.record("fuzzy_hit")
    snap = counters.snapshot()
    assert snap.dedup_fuzzy_hits == 1
    assert snap.dedup_tuple_hits == 0
    assert snap.dedup_url_hits == 0


def test_record_judge_pending_increments_judge_resumed_only(
    run_log: RunLog, display: FakeStatusDisplay
) -> None:
    counters = _make(run_log, display)
    counters.record("judge_pending")
    snap = counters.snapshot()
    assert snap.judge_resumed == 1
    assert snap.dedup_url_hits == 0
    assert snap.dedup_tuple_hits == 0
    assert snap.dedup_run_hits == 0
    assert snap.dedup_misses == 0


# ---------------------------------------------------------------------------
# snapshot() — derived skipped + frozen dataclass
# ---------------------------------------------------------------------------


def test_skipped_is_sum_of_hit_variants(
    run_log: RunLog, display: FakeStatusDisplay
) -> None:
    counters = _make(run_log, display)
    counters.record("url_hit")
    counters.record("url_hit")
    counters.record("tuple_hit")
    counters.record("fuzzy_hit")
    counters.record("run_hit")
    counters.record("miss")  # does not contribute to skipped
    counters.record("judge_pending")  # does not contribute to skipped
    snap = counters.snapshot()
    assert snap.skipped == 4


def test_snapshot_is_frozen(run_log: RunLog, display: FakeStatusDisplay) -> None:
    counters = _make(run_log, display)
    snap = counters.snapshot()
    assert isinstance(snap, DedupSnapshot)
    with pytest.raises((AttributeError, TypeError)):
        snap.dedup_misses = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# emit_run_complete() — aggregate event
# ---------------------------------------------------------------------------


def test_emit_run_complete_writes_event_with_counters(
    logs_dir: Path, run_log: RunLog, display: FakeStatusDisplay
) -> None:
    counters = _make(run_log, display)
    counters.record("url_hit")
    counters.record("url_hit")
    counters.record("tuple_hit")
    counters.record("fuzzy_hit")
    counters.record("run_hit")
    counters.record("miss")
    counters.record("judge_pending")
    counters.emit_run_complete()

    events = _read_events(logs_dir)
    assert len(events) == 1
    evt = events[0]
    assert evt["event"] == "run_complete"
    assert evt["dedup_url_hits"] == 2
    assert evt["dedup_tuple_hits"] == 1
    assert evt["dedup_fuzzy_hits"] == 1
    assert evt["dedup_run_hits"] == 1
    assert evt["dedup_misses"] == 1
    assert evt["judge_resumed"] == 1
