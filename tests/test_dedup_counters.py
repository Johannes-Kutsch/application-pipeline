from __future__ import annotations

from pathlib import Path

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline.dedup_counters import DedupCounters, DedupSnapshot
from application_pipeline.parser_log import RunLog


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path / "logs")


@pytest.fixture
def display() -> FakeStatusDisplay:
    d = FakeStatusDisplay()
    d.register("pipeline_dedup", order=10, phase="running")
    return d


def _make(run_log: RunLog, display: FakeStatusDisplay) -> DedupCounters:
    return DedupCounters(display=display, run_log=run_log)


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
# Status Display body publication
# ---------------------------------------------------------------------------


def test_status_display_body_published_after_record(
    run_log: RunLog, display: FakeStatusDisplay
) -> None:
    counters = _make(run_log, display)
    counters.record("url_hit")
    counters.record("url_hit")
    counters.record("tuple_hit")
    counters.record("run_hit")
    counters.record("run_hit")
    counters.record("miss")
    bodies = display.body_updates_for("pipeline_dedup")
    assert bodies, "expected pipeline_dedup body updates"
    assert bodies[-1] == "url_hits=2 tuple_hits=1 run_hits=2 misses=1"


def test_judge_pending_does_not_alter_body_counters(
    run_log: RunLog, display: FakeStatusDisplay
) -> None:
    counters = _make(run_log, display)
    counters.record("judge_pending")
    bodies = display.body_updates_for("pipeline_dedup")
    assert bodies[-1] == "url_hits=0 tuple_hits=0 run_hits=0 misses=0"
