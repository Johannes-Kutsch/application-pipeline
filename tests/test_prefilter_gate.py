from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline.parser_log import RunLog
from application_pipeline.prefilter_gate import PreFilterGate
from application_pipeline.run_metrics import RunMetrics


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@dataclass
class _Stub:
    url: str
    source: str = "test-source"


@dataclass
class _Position:
    stub: _Stub
    title: str = "Test Job"


def _make_position(
    url: str = "https://example.com/1",
    title: str = "Test Job",
    source: str = "test-source",
) -> _Position:
    return _Position(stub=_Stub(url=url, source=source), title=title)


@dataclass
class _FakeDedupStore:
    out_of_domain_calls: list[Any] = field(default_factory=list)

    def mark_out_of_domain(self, key: Any) -> None:
        self.out_of_domain_calls.append(key)


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


@pytest.fixture
def dedup() -> _FakeDedupStore:
    return _FakeDedupStore()


def _make_gate(
    run_log: RunLog,
    metrics: RunMetrics,
    dedup: _FakeDedupStore,
    blacklist: list[str] | None = None,
) -> PreFilterGate:
    return PreFilterGate(
        blacklist=blacklist if blacklist is not None else ["python", "senior"],
        dedup=dedup,
        metrics=metrics,
        run_log=run_log,
    )


def _read_transcripts(logs_dir: Path) -> list[dict]:
    path = logs_dir / "pipeline_prefilter.transcripts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_events(logs_dir: Path) -> list[dict]:
    path = logs_dir / "pipeline_prefilter.events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# admit() pass path
# ---------------------------------------------------------------------------


def test_admit_clean_title_returns_true(
    run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup)
    result = gate.admit(_make_position(title="Java Developer"))
    assert result is True


def test_admit_clean_title_writes_transcript(
    logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup)
    gate.admit(
        _make_position(
            url="https://example.com/job1",
            title="Java Developer",
            source="my-source",
        )
    )
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://example.com/job1"
    assert row["title"] == "Java Developer"
    assert row["source"] == "my-source"
    assert row["passes"] is True
    assert row["reason"] == "passed"
    assert row["blacklist_matches"] == []
    assert row["title_len"] == len("Java Developer")


def test_admit_clean_title_no_mark_out_of_domain(
    run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup)
    gate.admit(_make_position(title="Java Developer"))
    assert dedup.out_of_domain_calls == []


def test_admit_clean_title_metrics(
    run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup)
    gate.admit(_make_position(title="Java Developer"))
    summary = metrics.to_run_summary(duration_s=0.0)
    assert summary.prefilter_passed == 1
    assert summary.prefilter_considered == 1
    assert summary.prefilter_dropped == 0
    assert summary.prefilter_blacklist_hits == 0


# ---------------------------------------------------------------------------
# admit() blacklist drop path
# ---------------------------------------------------------------------------


def test_admit_blacklist_title_returns_false(
    run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup)
    result = gate.admit(_make_position(title="Senior Python Developer"))
    assert result is False


def test_admit_blacklist_title_writes_transcript(
    logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup, blacklist=["python"])
    gate.admit(
        _make_position(
            url="https://example.com/job2",
            title="Python Developer",
            source="src2",
        )
    )
    rows = _read_transcripts(logs_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["passes"] is False
    assert row["reason"] == "blacklist_drop"
    assert row["blacklist_matches"] == [{"term": "python"}]
    assert row["url"] == "https://example.com/job2"
    assert row["title"] == "Python Developer"
    assert row["source"] == "src2"
    assert row["title_len"] == len("Python Developer")


def test_admit_blacklist_title_calls_mark_out_of_domain(
    run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup, blacklist=["python"])
    position = _make_position(title="Python Developer")
    gate.admit(position)
    assert len(dedup.out_of_domain_calls) == 1
    assert dedup.out_of_domain_calls[0] is position.stub


def test_admit_blacklist_title_metrics(
    run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup, blacklist=["python"])
    gate.admit(_make_position(title="Python Developer"))
    summary = metrics.to_run_summary(duration_s=0.0)
    assert summary.prefilter_dropped == 1
    assert summary.prefilter_considered == 1
    assert summary.prefilter_passed == 0
    assert summary.prefilter_blacklist_hits == 1


def test_admit_multiple_blacklist_matches_in_transcript(
    logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup, blacklist=["python", "senior"])
    gate.admit(_make_position(title="Senior Python Developer"))
    rows = _read_transcripts(logs_dir)
    row = rows[0]
    assert row["passes"] is False
    terms = {m["term"] for m in row["blacklist_matches"]}
    assert "python" in terms
    assert "senior" in terms


# ---------------------------------------------------------------------------
# Title-only invariant
# ---------------------------------------------------------------------------


def test_admit_title_only_ignores_other_fields(
    run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    """Gate must only inspect position.title — no other fields."""
    gate = _make_gate(run_log, metrics, dedup, blacklist=["python"])

    @dataclass
    class _StubWithExtra:
        url: str = "https://example.com/extra"
        source: str = "src"
        description: str = "python developer job"

    @dataclass
    class _PositionWithExtra:
        stub: _StubWithExtra = field(default_factory=_StubWithExtra)
        title: str = "Java Developer"
        description: str = "python developer job"

    position = _PositionWithExtra()
    result = gate.admit(position)
    assert result is True


# ---------------------------------------------------------------------------
# emit_run_complete()
# ---------------------------------------------------------------------------


def test_emit_run_complete_writes_event_row(
    logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup, blacklist=["python", "senior"])

    gate.admit(_make_position(url="https://example.com/p1", title="Java Developer"))
    gate.admit(_make_position(url="https://example.com/p2", title="Python Developer"))
    gate.admit(_make_position(url="https://example.com/p3", title="Senior Engineer"))

    gate.emit_run_complete()

    events = _read_events(logs_dir)
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "run_complete"
    assert ev["blacklist_keyword_hits"] == "python=1 senior=1"
    assert ev["NEGATIVE_KEYWORDS_dead"] == "[]"


def test_emit_run_complete_dead_keywords_listed(
    logs_dir: Path, run_log: RunLog, metrics: RunMetrics, dedup: _FakeDedupStore
) -> None:
    gate = _make_gate(run_log, metrics, dedup, blacklist=["python", "senior", "rust"])

    gate.admit(_make_position(title="Python Developer"))  # hits python

    gate.emit_run_complete()

    events = _read_events(logs_dir)
    ev = events[0]
    # blacklist_keyword_hits lists all terms with their counts
    assert ev["blacklist_keyword_hits"] == "python=1 senior=0 rust=0"
    # NEGATIVE_KEYWORDS_dead lists only zero-hit terms
    assert ev["NEGATIVE_KEYWORDS_dead"] == "[senior, rust]"
