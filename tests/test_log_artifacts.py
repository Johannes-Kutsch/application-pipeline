"""Tests for the ADR-0024 log artifact layout.

Per ADR-0024, data/logs/ is laid out by reader:
- lifecycle.jsonl — status-display events (registered/phase_changed/removed), shared, includes component field
- run.log — tracebacks and SUMMARY OF SESSION blocks, shared, with === headers
- <comp>.events.jsonl — per-step structured events, per-component, no component field
- <comp>.transcripts.jsonl — unchanged
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import application_pipeline.parser_log as parser_log
from application_pipeline.status_display import PlainStatusDisplay

_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture(autouse=True)
def reset_logs():
    parser_log._logs_dir = None
    yield
    parser_log._logs_dir = None


# ---------------------------------------------------------------------------
# lifecycle.jsonl
# ---------------------------------------------------------------------------


def test_lifecycle_jsonl_contains_registered_event_with_component_field(
    tmp_path: Path,
) -> None:
    parser_log.configure(tmp_path)
    parser_log.record_lifecycle("pipeline", "registered", order=0, phase="running")

    lifecycle_file = tmp_path / "lifecycle.jsonl"
    assert lifecycle_file.exists()
    lines = lifecycle_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert _ISO8601_RE.match(row["ts"])
    assert row["event"] == "registered"
    assert row["component"] == "pipeline"
    assert row["order"] == 0
    assert row["phase"] == "running"


def test_lifecycle_jsonl_multiple_components_all_in_shared_file(
    tmp_path: Path,
) -> None:
    parser_log.configure(tmp_path)
    parser_log.record_lifecycle("startup", "registered", order=0, phase="starting")
    parser_log.record_lifecycle("pipeline", "registered", order=1, phase="starting")
    parser_log.record_lifecycle("startup", "phase_changed", phase="done")
    parser_log.record_lifecycle("pipeline", "removed")

    lifecycle_file = tmp_path / "lifecycle.jsonl"
    lines = lifecycle_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    rows = [json.loads(line) for line in lines]
    assert rows[0]["component"] == "startup"
    assert rows[0]["event"] == "registered"
    assert rows[1]["component"] == "pipeline"
    assert rows[1]["event"] == "registered"
    assert rows[2]["component"] == "startup"
    assert rows[2]["event"] == "phase_changed"
    assert rows[3]["component"] == "pipeline"
    assert rows[3]["event"] == "removed"


def test_record_lifecycle_without_configure_is_noop(tmp_path: Path) -> None:
    parser_log.record_lifecycle("pipeline", "registered", order=0, phase="running")
    assert not (tmp_path / "lifecycle.jsonl").exists()


# ---------------------------------------------------------------------------
# status_display routes lifecycle events to lifecycle.jsonl, not per-comp .log
# ---------------------------------------------------------------------------


def test_plain_status_display_register_writes_to_lifecycle_jsonl(
    tmp_path: Path,
) -> None:
    parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")

    lifecycle_file = tmp_path / "lifecycle.jsonl"
    assert lifecycle_file.exists()
    row = json.loads(lifecycle_file.read_text(encoding="utf-8").strip())
    assert row["event"] == "registered"
    assert row["component"] == "pipeline"


def test_plain_status_display_lifecycle_events_not_in_component_log(
    tmp_path: Path,
) -> None:
    parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")
    display.update_phase("pipeline", phase="done")
    display.remove("pipeline")

    assert not (tmp_path / "pipeline.log").exists()


# ---------------------------------------------------------------------------
# run.log — SUMMARY OF SESSION blocks
# ---------------------------------------------------------------------------


def test_summarize_writes_to_run_log_with_header(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    started = datetime(2026, 5, 12, 15, 30, 0, tzinfo=timezone.utc)
    parser_log.summarize(
        "bundesagentur_api", {"discovered": 12, "duration_s": 47.3}, started
    )

    run_log = tmp_path / "run.log"
    assert run_log.exists()
    content = run_log.read_text(encoding="utf-8")
    assert "=== bundesagentur_api" in content
    assert "2026-05-12T15:30:00Z" in content
    assert "summary" in content
    assert "SUMMARY OF SESSION" in content
    assert "discovered=12" in content


# ---------------------------------------------------------------------------
# <comp>.events.jsonl — structured per-step events, no component field
# ---------------------------------------------------------------------------


def test_record_writes_jsonl_row_to_events_file(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record("bundesagentur_api", "discover_page", q="Python", page=1)

    events_file = tmp_path / "bundesagentur_api.events.jsonl"
    assert events_file.exists()
    row = json.loads(events_file.read_text(encoding="utf-8").strip())
    assert _ISO8601_RE.match(row["ts"])
    assert row["event"] == "discover_page"
    assert row["q"] == "Python"
    assert row["page"] == 1
    assert "component" not in row


def test_record_without_configure_is_noop(tmp_path: Path) -> None:
    parser_log.record("bundesagentur_api", "discover_page")
    assert not (tmp_path / "bundesagentur_api.events.jsonl").exists()


# ---------------------------------------------------------------------------
# run.log — tracebacks
# ---------------------------------------------------------------------------


def test_traceback_writes_to_run_log_with_header(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record_traceback(
        "bundesagentur_api",
        "Traceback (most recent call last):\n  File ...\nValueError: oops\n",
    )

    run_log = tmp_path / "run.log"
    assert run_log.exists()
    content = run_log.read_text(encoding="utf-8")
    assert "=== bundesagentur_api" in content
    assert "traceback" in content
    assert "Traceback (most recent call last):" in content
    assert "ValueError: oops" in content


def test_traceback_does_not_write_to_component_log(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record_traceback("bundesagentur_api", "Traceback...\nValueError\n")

    assert not (tmp_path / "bundesagentur_api.log").exists()


# ---------------------------------------------------------------------------
# No ghost .log files for lifecycle-only components
# ---------------------------------------------------------------------------


def test_lifecycle_only_component_produces_no_per_component_log(
    tmp_path: Path,
) -> None:
    parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("startup", order=0, phase="starting")
    display.update_phase("startup", phase="done")
    display.remove("startup")

    assert not (tmp_path / "startup.log").exists()
    assert not (tmp_path / "startup.events.jsonl").exists()
    assert (tmp_path / "lifecycle.jsonl").exists()
