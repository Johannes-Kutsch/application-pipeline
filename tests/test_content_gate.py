from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from application_pipeline.content_gate import ContentGate, ContentSnapshot
from application_pipeline.parser_log import RunLog


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@dataclass
class _Stub:
    url: str = "https://example.com/1"
    source: str = "test-source"
    title: str | None = "Test Job"


@pytest.fixture
def logs_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def run_log(logs_dir: Path) -> RunLog:
    return RunLog(logs_dir)


def _make_gate(run_log: RunLog) -> ContentGate:
    return ContentGate(run_log=run_log)


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
# inspect() — decision surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "passes", "reason"),
    [
        ("x" * 100, True, "passed"),
        ("x" * 99, False, "too_short"),
        ("   \n\t  ", False, "empty_body"),
    ],
)
def test_inspect_returns_content_decision_and_writes_matching_transcript(
    logs_dir: Path,
    run_log: RunLog,
    body: str,
    passes: bool,
    reason: str,
) -> None:
    gate = _make_gate(run_log)

    decision = gate.inspect(body, _Stub(url="https://example.com/inspect"))
    rows = _read_transcripts(logs_dir)

    assert decision.passes is passes
    assert decision.reason == reason
    assert rows == [
        {
            "url": "https://example.com/inspect",
            "title": "Test Job",
            "source": "test-source",
            "passes": passes,
            "reason": reason,
            "body_len": len(body),
        }
    ]


# ---------------------------------------------------------------------------
# admit() return value — pass/drop behavior
# ---------------------------------------------------------------------------


def test_admit_non_empty_body_returns_true(run_log: RunLog) -> None:
    gate = _make_gate(run_log)
    assert gate.admit("x" * 100, _Stub()) is True


def test_admit_empty_body_returns_false(run_log: RunLog) -> None:
    gate = _make_gate(run_log)
    assert gate.admit("", _Stub()) is False


def test_admit_whitespace_only_body_returns_false(run_log: RunLog) -> None:
    gate = _make_gate(run_log)
    assert gate.admit("   \n\t  ", _Stub()) is False


# ---------------------------------------------------------------------------
# Transcript rows
# ---------------------------------------------------------------------------


def test_admit_writes_transcript_for_passing_position(
    logs_dir: Path, run_log: RunLog
) -> None:
    gate = _make_gate(run_log)
    stub = _Stub(url="https://example.com/job1", title="Engineer", source="my-source")
    body = "We are looking for an engineer to join our team. " + "x" * 60
    gate.admit(body, stub)
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://example.com/job1"
    assert row["title"] == "Engineer"
    assert row["source"] == "my-source"
    assert row["passes"] is True
    assert row["reason"] == "passed"
    assert row["body_len"] == len(body)


def test_admit_writes_transcript_for_dropped_position(
    logs_dir: Path, run_log: RunLog
) -> None:
    gate = _make_gate(run_log)
    stub = _Stub(
        url="https://example.com/empty", title="Empty Job", source="some-source"
    )
    gate.admit("", stub)
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://example.com/empty"
    assert row["passes"] is False
    assert row["reason"] == "empty_body"
    assert row["body_len"] == 0


def test_admit_body_len_is_pre_strip_length(logs_dir: Path, run_log: RunLog) -> None:
    gate = _make_gate(run_log)
    gate.admit("   ", _Stub())
    rows = _read_transcripts(logs_dir)
    assert rows[0]["body_len"] == 3  # pre-strip length


# ---------------------------------------------------------------------------
# emit_run_complete() — aggregate event
# ---------------------------------------------------------------------------


def test_emit_run_complete_writes_event_with_counters(
    logs_dir: Path, run_log: RunLog
) -> None:
    gate = _make_gate(run_log)
    gate.admit("x" * 100, _Stub(url="https://example.com/1"))
    gate.admit("x" * 100, _Stub(url="https://example.com/2"))
    gate.admit("", _Stub(url="https://example.com/3"))
    gate.emit_run_complete()

    events = _read_events(logs_dir)
    assert len(events) == 1
    evt = events[0]
    assert evt["event"] == "run_complete"
    assert evt["content_considered"] == 3
    assert evt["content_passed"] == 2
    assert evt["content_dropped_empty_body"] == 1


def test_emit_run_complete_counters_reconcile_with_transcripts(
    logs_dir: Path, run_log: RunLog
) -> None:
    gate = _make_gate(run_log)
    gate.admit("x" * 100, _Stub(url="https://example.com/1"))
    gate.admit("  ", _Stub(url="https://example.com/2"))
    gate.emit_run_complete()

    transcripts = _read_transcripts(logs_dir)
    events = _read_events(logs_dir)
    evt = events[0]

    passed_in_transcripts = sum(1 for r in transcripts if r["passes"])
    empty_body_in_transcripts = sum(
        1 for r in transcripts if r["reason"] == "empty_body"
    )

    assert evt["content_considered"] == len(transcripts)
    assert evt["content_passed"] == passed_in_transcripts
    assert evt["content_dropped_empty_body"] == empty_body_in_transcripts


# ---------------------------------------------------------------------------
# snapshot() — ContentSnapshot
# ---------------------------------------------------------------------------


def test_snapshot_counters_updated_on_pass(run_log: RunLog) -> None:
    gate = _make_gate(run_log)
    gate.admit("x" * 100, _Stub())
    snap = gate.snapshot()
    assert snap.content_considered == 1
    assert snap.content_passed == 1
    assert snap.content_dropped_empty_body == 0


def test_snapshot_counters_updated_on_drop(run_log: RunLog) -> None:
    gate = _make_gate(run_log)
    gate.admit("", _Stub())
    snap = gate.snapshot()
    assert snap.content_considered == 1
    assert snap.content_passed == 0
    assert snap.content_dropped_empty_body == 1


def test_snapshot_is_frozen_dataclass(run_log: RunLog) -> None:
    gate = _make_gate(run_log)
    snap = gate.snapshot()
    assert isinstance(snap, ContentSnapshot)
    with pytest.raises((AttributeError, TypeError)):
        snap.content_considered = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# too_short: admit() return value
# ---------------------------------------------------------------------------


def test_admit_short_body_returns_false(run_log: RunLog) -> None:
    gate = _make_gate(run_log)
    # 99 chars: non-empty but below 100-char floor
    short_body = "x" * 99
    assert gate.admit(short_body, _Stub()) is False


def test_admit_body_of_exactly_100_chars_returns_true(run_log: RunLog) -> None:
    gate = _make_gate(run_log)
    assert gate.admit("x" * 100, _Stub()) is True


# ---------------------------------------------------------------------------
# too_short: transcript rows
# ---------------------------------------------------------------------------


def test_admit_short_body_writes_transcript_with_too_short_reason(
    logs_dir: Path, run_log: RunLog
) -> None:
    gate = _make_gate(run_log)
    short_body = "x" * 99
    gate.admit(short_body, _Stub())
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["passes"] is False
    assert row["reason"] == "too_short"
    assert row["body_len"] == 99


def test_admit_empty_body_transcript_still_has_empty_body_reason(
    logs_dir: Path, run_log: RunLog
) -> None:
    gate = _make_gate(run_log)
    gate.admit("   ", _Stub())
    rows = _read_transcripts(logs_dir)
    assert rows[0]["reason"] == "empty_body"


# ---------------------------------------------------------------------------
# too_short: snapshot counter
# ---------------------------------------------------------------------------


def test_snapshot_content_dropped_too_short_increments_on_short_body(
    run_log: RunLog,
) -> None:
    gate = _make_gate(run_log)
    gate.admit("x" * 99, _Stub())
    snap = gate.snapshot()
    assert snap.content_dropped_too_short == 1
    assert snap.content_dropped_empty_body == 0


# ---------------------------------------------------------------------------
# too_short: emit_run_complete event
# ---------------------------------------------------------------------------


def test_emit_run_complete_includes_content_dropped_too_short(
    logs_dir: Path, run_log: RunLog
) -> None:
    gate = _make_gate(run_log)
    gate.admit("x" * 100, _Stub(url="https://example.com/1"))
    gate.admit("x" * 99, _Stub(url="https://example.com/2"))
    gate.admit("", _Stub(url="https://example.com/3"))
    gate.emit_run_complete()

    events = _read_events(logs_dir)
    evt = events[0]
    assert evt["content_dropped_too_short"] == 1
    assert evt["content_dropped_empty_body"] == 1
    assert evt["content_passed"] == 1


# ---------------------------------------------------------------------------
# too_short: empty_body counter unchanged by short-body drops
# ---------------------------------------------------------------------------


def test_empty_body_counter_not_incremented_by_short_body(run_log: RunLog) -> None:
    gate = _make_gate(run_log)
    gate.admit("x" * 99, _Stub())
    snap = gate.snapshot()
    assert snap.content_dropped_empty_body == 0
