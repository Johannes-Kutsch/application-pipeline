"""Tests for the generalised component log interface (record, record_transcript, summarize).

Covers the LLM call-site use-case described in issue #184, exercised through
parser_log — the single module that owns both file shapes.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import application_pipeline.parser_log as parser_log

_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


@pytest.fixture(autouse=True)
def reset_logs():
    parser_log._logs_dir = None
    yield
    parser_log._logs_dir = None


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def test_record_creates_timestamped_event_line(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record("classify_relevance", "batch_sent")

    events_file = tmp_path / "classify_relevance.events.jsonl"
    assert events_file.exists()
    row = json.loads(events_file.read_text(encoding="utf-8").strip())
    assert _ISO8601_RE.match(row["ts"])
    assert row["event"] == "batch_sent"


def test_record_appends_key_value_fields(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record(
        "classify_relevance", "batch_malformed", batch_id="b42", reason="bad_json"
    )

    row = json.loads(
        (tmp_path / "classify_relevance.events.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert row["event"] == "batch_malformed"
    assert row["batch_id"] == "b42"
    assert row["reason"] == "bad_json"


def test_record_without_configure_is_noop(tmp_path: Path) -> None:
    parser_log.record("classify_relevance", "batch_sent")
    assert not (tmp_path / "classify_relevance.events.jsonl").exists()


def test_record_multiple_calls_append_in_order(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record("judge_match", "session_start")
    parser_log.record("judge_match", "cli_error", exit_code="1")

    lines = (
        (tmp_path / "judge_match.events.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "session_start"
    assert json.loads(lines[1])["event"] == "cli_error"


def test_record_each_line_has_iso8601_timestamp(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    for event in ("e1", "e2", "e3"):
        parser_log.record("classify_relevance", event)

    lines = (
        (tmp_path / "classify_relevance.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert all(_ISO8601_RE.match(json.loads(line)["ts"]) for line in lines)


# ---------------------------------------------------------------------------
# record_transcript
# ---------------------------------------------------------------------------


def test_record_transcript_appends_valid_json_object(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    entry = {
        "ts": "2026-05-12T10:00:00Z",
        "language": "Python",
        "status": "ok",
        "cost_usd": 0.001,
    }
    parser_log.record_transcript("classify_relevance", entry)

    transcript_file = tmp_path / "classify_relevance.transcripts.jsonl"
    assert transcript_file.exists()
    lines = transcript_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == entry


def test_record_transcript_multiple_calls_one_json_per_line(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    entries: list[dict[str, object]] = [
        {"ts": "2026-05-12T10:00:00Z", "status": "ok", "item_ids": [1, 2]},
        {"ts": "2026-05-12T10:01:00Z", "status": "error", "item_ids": []},
        {"ts": "2026-05-12T10:02:00Z", "status": "ok", "item_ids": [3]},
    ]
    for e in entries:
        parser_log.record_transcript("classify_relevance", e)

    lines = (
        (tmp_path / "classify_relevance.transcripts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(lines) == 3
    for raw, expected in zip(lines, entries):
        assert json.loads(raw) == expected


def test_record_transcript_without_configure_is_noop(tmp_path: Path) -> None:
    parser_log.record_transcript("classify_relevance", {"ts": "2026-05-12T10:00:00Z"})
    assert not (tmp_path / "classify_relevance.transcripts.jsonl").exists()


def test_record_transcript_preserves_all_entry_fields(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    entry = {
        "ts": "2026-05-12T10:00:00Z",
        "language": "Rust",
        "prompt": "classify this",
        "response": "in_domain",
        "parsed": {"in_domain": True},
        "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0},
        "cost_usd": 0.002,
        "duration_s": 1.23,
        "status": "ok",
        "item_ids": [10, 20],
        "stub_urls": ["https://example.com/a", "https://example.com/b"],
    }
    parser_log.record_transcript("classify_relevance", entry)

    raw = (tmp_path / "classify_relevance.transcripts.jsonl").read_text(
        encoding="utf-8"
    )
    assert json.loads(raw.strip()) == entry


def test_record_transcript_null_parsed_field_survives_roundtrip(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    entry = {"ts": "2026-05-12T10:00:00Z", "parsed": None, "status": "error"}
    parser_log.record_transcript("judge_match", entry)

    raw = (tmp_path / "judge_match.transcripts.jsonl").read_text(encoding="utf-8")
    assert json.loads(raw.strip())["parsed"] is None


# ---------------------------------------------------------------------------
# summarize — generalised counter schema
# ---------------------------------------------------------------------------


def test_summarize_with_caller_supplied_counts(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    started = datetime(2026, 5, 12, 15, 30, 0, tzinfo=timezone.utc)
    counts = {
        "batches_sent": 10,
        "items_classified": 50,
        "in_domain": 35,
        "off_domain": 15,
        "batches_failed": 1,
        "input_tokens": 8000,
        "output_tokens": 2000,
        "cache_read_tokens": 500,
        "cost_usd": 0.05,
        "duration_s": 42.7,
    }
    parser_log.summarize("classify_relevance", counts, started)

    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "SUMMARY OF SESSION 2026-05-12T15:30:00Z" in content
    for key, value in counts.items():
        assert f"{key}={value}" in content


def test_summarize_with_zero_events_produces_valid_trailer(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    started = datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
    parser_log.summarize("judge_match", {"calls": 0, "duration_s": 0.0}, started)

    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "SUMMARY OF SESSION" in content
    assert "calls=0" in content
    assert "duration_s=0.0" in content


def test_two_sessions_produce_two_summary_blocks_separated_by_blank_line(
    tmp_path: Path,
) -> None:
    parser_log.configure(tmp_path)
    started1 = datetime(2026, 5, 12, 8, 0, 0, tzinfo=timezone.utc)
    started2 = datetime(2026, 5, 12, 16, 0, 0, tzinfo=timezone.utc)

    # Session 1
    parser_log.record("classify_relevance", "batch_sent", batch_id="b1")
    parser_log.summarize(
        "classify_relevance", {"batches_sent": 1, "items_classified": 5}, started1
    )

    # Session 2
    parser_log.record("classify_relevance", "batch_sent", batch_id="b2")
    parser_log.summarize(
        "classify_relevance", {"batches_sent": 1, "items_classified": 3}, started2
    )

    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert content.count("SUMMARY OF SESSION") == 2
    assert "2026-05-12T08:00:00Z" in content
    assert "2026-05-12T16:00:00Z" in content

    first_idx = content.index("SUMMARY OF SESSION 2026-05-12T08:00:00Z")
    second_idx = content.index("SUMMARY OF SESSION 2026-05-12T16:00:00Z")
    between = content[first_idx:second_idx]
    assert "\n\n" in between, (
        "SUMMARY blocks must be separated by at least one blank line"
    )


def test_event_log_and_transcript_are_independent_files(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record("classify_relevance", "batch_sent")
    parser_log.record_transcript(
        "classify_relevance", {"ts": "2026-05-12T10:00:00Z", "status": "ok"}
    )

    assert (tmp_path / "classify_relevance.events.jsonl").exists()
    assert (tmp_path / "classify_relevance.transcripts.jsonl").exists()


def test_different_component_ids_write_separate_files(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record("classify_relevance", "batch_sent")
    parser_log.record_transcript("judge_match", {"ts": "2026-05-12T10:00:00Z"})

    assert (tmp_path / "classify_relevance.events.jsonl").exists()
    assert (tmp_path / "judge_match.transcripts.jsonl").exists()
    assert not (tmp_path / "judge_match.events.jsonl").exists()
    assert not (tmp_path / "classify_relevance.transcripts.jsonl").exists()
