from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline.content_gate import ContentGate
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


@dataclass
class _Position:
    stub: _Stub
    title: str = "Test Job"
    raw_description: str = "Some body text"


def _make_position(
    url: str = "https://example.com/1",
    title: str = "Test Job",
    raw_description: str = "Some body text",
    source: str = "test-source",
) -> _Position:
    return _Position(
        stub=_Stub(url=url, source=source, title=title),
        title=title,
        raw_description=raw_description,
    )


@pytest.fixture
def logs_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def run_log(logs_dir: Path) -> RunLog:
    return RunLog(logs_dir)


@pytest.fixture
def metrics(run_log: RunLog) -> RunMetrics:
    display = FakeStatusDisplay()
    m = RunMetrics(display, run_log=run_log)
    m.register_rows(starting_order=0)
    return m


def _make_gate(run_log: RunLog, metrics: RunMetrics) -> ContentGate:
    return ContentGate(metrics=metrics, run_log=run_log)


def _read_transcripts(logs_dir: Path) -> list[dict]:
    path = logs_dir / "pipeline" / "content.transcripts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_events(logs_dir: Path) -> list[dict]:
    path = logs_dir / "pipeline" / "content.events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# admit() return value — pass/drop behavior
# ---------------------------------------------------------------------------


def test_admit_non_empty_body_returns_true(
    run_log: RunLog, metrics: RunMetrics
) -> None:
    gate = _make_gate(run_log, metrics)
    position = _make_position(raw_description="Some body text")
    assert gate.admit(position) is True


def test_admit_empty_body_returns_false(run_log: RunLog, metrics: RunMetrics) -> None:
    gate = _make_gate(run_log, metrics)
    position = _make_position(raw_description="")
    assert gate.admit(position) is False


def test_admit_whitespace_only_body_returns_false(
    run_log: RunLog, metrics: RunMetrics
) -> None:
    gate = _make_gate(run_log, metrics)
    position = _make_position(raw_description="   \n\t  ")
    assert gate.admit(position) is False


# ---------------------------------------------------------------------------
# Transcript rows
# ---------------------------------------------------------------------------


def test_admit_writes_transcript_for_passing_position(
    logs_dir: Path, run_log: RunLog, metrics: RunMetrics
) -> None:
    gate = _make_gate(run_log, metrics)
    position = _make_position(
        url="https://example.com/job1",
        title="Engineer",
        raw_description="We are looking for an engineer.",
        source="my-source",
    )
    gate.admit(position)
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://example.com/job1"
    assert row["title"] == "Engineer"
    assert row["source"] == "my-source"
    assert row["passes"] is True
    assert row["reason"] == "passed"
    assert row["body_len"] == len("We are looking for an engineer.")


def test_admit_writes_transcript_for_dropped_position(
    logs_dir: Path, run_log: RunLog, metrics: RunMetrics
) -> None:
    gate = _make_gate(run_log, metrics)
    position = _make_position(
        url="https://example.com/empty",
        title="Empty Job",
        raw_description="",
        source="some-source",
    )
    gate.admit(position)
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://example.com/empty"
    assert row["passes"] is False
    assert row["reason"] == "empty_body"
    assert row["body_len"] == 0


def test_admit_body_len_is_pre_strip_length(
    logs_dir: Path, run_log: RunLog, metrics: RunMetrics
) -> None:
    gate = _make_gate(run_log, metrics)
    position = _make_position(raw_description="   ")
    gate.admit(position)
    rows = _read_transcripts(logs_dir)
    assert rows[0]["body_len"] == 3  # pre-strip length


# ---------------------------------------------------------------------------
# emit_run_complete() — aggregate event
# ---------------------------------------------------------------------------


def test_emit_run_complete_writes_event_with_counters(
    logs_dir: Path, run_log: RunLog, metrics: RunMetrics
) -> None:
    gate = _make_gate(run_log, metrics)
    gate.admit(_make_position(raw_description="has body"))
    gate.admit(
        _make_position(raw_description="also has body", url="https://example.com/2")
    )
    gate.admit(_make_position(raw_description="", url="https://example.com/3"))
    gate.emit_run_complete()

    events = _read_events(logs_dir)
    assert len(events) == 1
    evt = events[0]
    assert evt["event"] == "run_complete"
    assert evt["content_considered"] == 3
    assert evt["content_passed"] == 2
    assert evt["content_dropped_empty_body"] == 1


def test_emit_run_complete_counters_reconcile_with_transcripts(
    logs_dir: Path, run_log: RunLog, metrics: RunMetrics
) -> None:
    gate = _make_gate(run_log, metrics)
    gate.admit(_make_position(raw_description="body", url="https://example.com/1"))
    gate.admit(_make_position(raw_description="  ", url="https://example.com/2"))
    gate.emit_run_complete()

    transcripts = _read_transcripts(logs_dir)
    events = _read_events(logs_dir)
    evt = events[0]

    passed_in_transcripts = sum(1 for r in transcripts if r["passes"])
    dropped_in_transcripts = sum(1 for r in transcripts if not r["passes"])

    assert evt["content_considered"] == len(transcripts)
    assert evt["content_passed"] == passed_in_transcripts
    assert evt["content_dropped_empty_body"] == dropped_in_transcripts


# ---------------------------------------------------------------------------
# RunMetrics counters
# ---------------------------------------------------------------------------


def test_metrics_content_counters_updated_on_pass(
    run_log: RunLog, metrics: RunMetrics
) -> None:
    gate = _make_gate(run_log, metrics)
    gate.admit(_make_position(raw_description="has body"))
    summary = metrics.to_run_summary(duration_s=0.0)
    assert summary.content_considered == 1
    assert summary.content_passed == 1
    assert summary.content_dropped_empty_body == 0


def test_metrics_content_counters_updated_on_drop(
    run_log: RunLog, metrics: RunMetrics
) -> None:
    gate = _make_gate(run_log, metrics)
    gate.admit(_make_position(raw_description=""))
    summary = metrics.to_run_summary(duration_s=0.0)
    assert summary.content_considered == 1
    assert summary.content_passed == 0
    assert summary.content_dropped_empty_body == 1


# ---------------------------------------------------------------------------
# Status Display row
# ---------------------------------------------------------------------------


def test_status_display_has_pipeline_content_row(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    m = RunMetrics(display, run_log=run_log)
    m.register_rows(starting_order=0)
    assert "pipeline_content" in display.registered_names()


def test_status_display_body_reflects_content_counters(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    m = RunMetrics(display, run_log=run_log)
    m.register_rows(starting_order=0)
    gate = ContentGate(metrics=m, run_log=run_log)
    gate.admit(_make_position(raw_description="has body"))
    gate.admit(_make_position(raw_description="", url="https://example.com/2"))
    bodies = display.body_updates_for("pipeline_content")
    assert bodies, "expected at least one body update for pipeline_content"
    last_body = bodies[-1]
    assert "considered=2" in last_body
    assert "passed=1" in last_body
    assert "dropped=1" in last_body
