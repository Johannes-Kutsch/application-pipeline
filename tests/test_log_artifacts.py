"""Tests for the ADR-0045 log artifact layout.

Per ADR-0045, data/logs/ is laid out as:
- lifecycle.jsonl — status-display events (registered/phase_changed/removed), shared, includes component field
- run.log — tracebacks and SUMMARY OF SESSION blocks, shared, with === headers
- <layer>/<rest>.events.jsonl — per-step structured events, per-component, no component field
- <layer>/<rest>.transcripts.jsonl — LLM transcripts, per-component
  where <layer> is parser/, llm/, or pipeline/ and <rest> is the component id with layer prefix stripped
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from application_pipeline.parser_log import RunLog
from application_pipeline.status_display import PlainStatusDisplay

_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---------------------------------------------------------------------------
# lifecycle.jsonl
# ---------------------------------------------------------------------------


def test_lifecycle_jsonl_contains_registered_event_with_component_field(
    tmp_path: Path,
) -> None:
    log = RunLog(tmp_path)
    log.lifecycle("pipeline", "registered", order=0, phase="running")

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
    log = RunLog(tmp_path)
    log.lifecycle("startup", "registered", order=0, phase="starting")
    log.lifecycle("pipeline", "registered", order=1, phase="starting")
    log.lifecycle("startup", "phase_changed", phase="done")
    log.lifecycle("pipeline", "removed")

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


# ---------------------------------------------------------------------------
# status_display routes lifecycle events to lifecycle.jsonl, not per-comp .log
# ---------------------------------------------------------------------------


def test_plain_status_display_register_writes_to_lifecycle_jsonl(
    tmp_path: Path,
) -> None:
    display = PlainStatusDisplay(run_log=RunLog(tmp_path))
    display.register("pipeline", order=0, phase="running")

    lifecycle_file = tmp_path / "lifecycle.jsonl"
    assert lifecycle_file.exists()
    row = json.loads(lifecycle_file.read_text(encoding="utf-8").strip())
    assert row["event"] == "registered"
    assert row["component"] == "pipeline"


def test_plain_status_display_lifecycle_events_not_in_component_log(
    tmp_path: Path,
) -> None:
    display = PlainStatusDisplay(run_log=RunLog(tmp_path))
    display.register("pipeline", order=0, phase="running")
    display.update_phase("pipeline", phase="done")
    display.remove("pipeline")

    assert not (tmp_path / "pipeline.log").exists()


# ---------------------------------------------------------------------------
# run.log — SUMMARY OF SESSION blocks
# ---------------------------------------------------------------------------


def test_summarize_writes_to_run_log_with_header(tmp_path: Path) -> None:
    log = RunLog(tmp_path)
    started = datetime(2026, 5, 12, 15, 30, 0, tzinfo=timezone.utc)
    log.summary(
        "parser_bundesagentur_api", {"discovered": 12, "duration_s": 47.3}, started
    )

    run_log_file = tmp_path / "run.log"
    assert run_log_file.exists()
    content = run_log_file.read_text(encoding="utf-8")
    assert "=== parser_bundesagentur_api" in content
    assert "2026-05-12T15:30:00Z" in content
    assert "summary" in content
    assert "SUMMARY OF SESSION" in content
    assert "discovered=12" in content


# ---------------------------------------------------------------------------
# <layer>/<rest>.events.jsonl — structured per-step events, no component field
# ---------------------------------------------------------------------------


def test_record_writes_jsonl_row_to_events_file(tmp_path: Path) -> None:
    log = RunLog(tmp_path)
    log.event("parser_bundesagentur_api", "discover_page", q="Python", page=1)

    events_file = tmp_path / "parser" / "bundesagentur_api.events.jsonl"
    assert events_file.exists()
    row = json.loads(events_file.read_text(encoding="utf-8").strip())
    assert _ISO8601_RE.match(row["ts"])
    assert row["event"] == "discover_page"
    assert row["q"] == "Python"
    assert row["page"] == 1
    assert "component" not in row
    assert not (tmp_path / "parser_bundesagentur_api.events.jsonl").exists()


def test_event_rows_route_each_prefixed_layer_to_subdir_with_stripped_filename(
    tmp_path: Path,
) -> None:
    log = RunLog(tmp_path)
    log.event("parser_bundesagentur_api", "discover_started", page=1)
    log.event("llm_classify_relevance", "batch_sent", batch_id="b1")
    log.event("pipeline_run_metrics", "run_complete", matched=5)

    parser_row = json.loads(
        (tmp_path / "parser" / "bundesagentur_api.events.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    llm_row = json.loads(
        (tmp_path / "llm" / "classify_relevance.events.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    pipeline_row = json.loads(
        (tmp_path / "pipeline" / "run_metrics.events.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )

    assert _ISO8601_RE.match(parser_row["ts"])
    assert parser_row["event"] == "discover_started"
    assert parser_row["page"] == 1
    assert "component" not in parser_row
    assert not (tmp_path / "parser_bundesagentur_api.events.jsonl").exists()

    assert _ISO8601_RE.match(llm_row["ts"])
    assert llm_row["event"] == "batch_sent"
    assert llm_row["batch_id"] == "b1"
    assert "component" not in llm_row
    assert not (tmp_path / "llm_classify_relevance.events.jsonl").exists()

    assert _ISO8601_RE.match(pipeline_row["ts"])
    assert pipeline_row["event"] == "run_complete"
    assert pipeline_row["matched"] == 5
    assert "component" not in pipeline_row
    assert not (tmp_path / "pipeline_run_metrics.events.jsonl").exists()


def test_event_rows_for_prefixed_component_append_one_json_object_per_line_in_call_order(
    tmp_path: Path,
) -> None:
    log = RunLog(tmp_path)
    log.event("parser_bundesagentur_api", "discover_started", page=1)
    log.event("parser_bundesagentur_api", "discover_finished", page=1, found=25)

    lines = (
        (tmp_path / "parser" / "bundesagentur_api.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    rows = [json.loads(line) for line in lines]

    assert len(rows) == 2
    assert _ISO8601_RE.match(rows[0]["ts"])
    assert rows[0] == {
        "ts": rows[0]["ts"],
        "event": "discover_started",
        "page": 1,
    }
    assert _ISO8601_RE.match(rows[1]["ts"])
    assert rows[1] == {
        "ts": rows[1]["ts"],
        "event": "discover_finished",
        "page": 1,
        "found": 25,
    }


def test_unprefixed_event_rows_keep_existing_root_file_behavior(tmp_path: Path) -> None:
    log = RunLog(tmp_path)
    log.event("startup", "phase_started", phase="bootstrap")

    events_file = tmp_path / "startup.events.jsonl"
    assert events_file.exists()

    row = json.loads(events_file.read_text(encoding="utf-8").strip())
    assert _ISO8601_RE.match(row["ts"])
    assert row["event"] == "phase_started"
    assert row["phase"] == "bootstrap"
    assert "component" not in row
    assert not (tmp_path / "startup" / "phase_started.events.jsonl").exists()


# ---------------------------------------------------------------------------
# <layer>/<rest>.transcripts.jsonl — transcript rows, one JSON object per line
# ---------------------------------------------------------------------------


def test_transcript_rows_for_prefixed_component_preserve_nested_and_null_fields(
    tmp_path: Path,
) -> None:
    log = RunLog(tmp_path)
    entry = {
        "prompt": {"system": "triage", "messages": ["first", None]},
        "response": {"matches": True, "reason": None},
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "cost_usd": None,
    }

    log.transcript("llm_classify_relevance", entry)

    transcript_file = tmp_path / "llm" / "classify_relevance.transcripts.jsonl"
    assert transcript_file.exists()
    assert json.loads(transcript_file.read_text(encoding="utf-8").strip()) == entry
    assert not (tmp_path / "llm_classify_relevance.transcripts.jsonl").exists()


def test_transcript_rows_append_in_call_order_and_stay_independent_from_events(
    tmp_path: Path,
) -> None:
    log = RunLog(tmp_path)
    log.event("llm_classify_relevance", "batch_sent", batch_id="b1")
    entries: list[dict[str, object]] = [
        {"status": "ok", "item_ids": [1, 2]},
        {"status": "error", "item_ids": [], "parsed": None},
    ]

    for entry in entries:
        log.transcript("llm_classify_relevance", entry)

    transcript_file = tmp_path / "llm" / "classify_relevance.transcripts.jsonl"
    assert transcript_file.exists()
    assert [
        json.loads(line)
        for line in transcript_file.read_text(encoding="utf-8").splitlines()
    ] == entries

    event_file = tmp_path / "llm" / "classify_relevance.events.jsonl"
    assert event_file.exists()
    assert len(event_file.read_text(encoding="utf-8").splitlines()) == 1


# ---------------------------------------------------------------------------
# run.log — tracebacks
# ---------------------------------------------------------------------------


def test_traceback_writes_to_run_log_with_header(tmp_path: Path) -> None:
    log = RunLog(tmp_path)
    log.traceback(
        "parser_bundesagentur_api",
        "Traceback (most recent call last):\n  File ...\nValueError: oops\n",
    )

    run_log_file = tmp_path / "run.log"
    assert run_log_file.exists()
    content = run_log_file.read_text(encoding="utf-8")
    assert "=== parser_bundesagentur_api" in content
    assert "traceback" in content
    assert "Traceback (most recent call last):" in content
    assert "ValueError: oops" in content


def test_traceback_does_not_write_to_component_log(tmp_path: Path) -> None:
    log = RunLog(tmp_path)
    log.traceback("parser_bundesagentur_api", "Traceback...\nValueError\n")

    assert not (tmp_path / "parser_bundesagentur_api.log").exists()


# ---------------------------------------------------------------------------
# No ghost .log files for lifecycle-only components
# ---------------------------------------------------------------------------


def test_lifecycle_only_component_produces_no_per_component_log(
    tmp_path: Path,
) -> None:
    display = PlainStatusDisplay(run_log=RunLog(tmp_path))
    display.register("startup", order=0, phase="starting")
    display.update_phase("startup", phase="done")
    display.remove("startup")

    assert not (tmp_path / "startup.log").exists()
    assert not (tmp_path / "startup.events.jsonl").exists()
    assert (tmp_path / "lifecycle.jsonl").exists()
