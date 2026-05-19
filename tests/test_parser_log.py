from __future__ import annotations

import json
import os
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
    parser_log.record("myparser", "parser_started")

    events_file = tmp_path / "myparser.events.jsonl"
    assert events_file.exists()
    row = json.loads(events_file.read_text(encoding="utf-8").strip())
    assert _ISO8601_RE.match(row["ts"])
    assert row["event"] == "parser_started"


def test_record_appends_key_value_fields(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record(
        "p", "enrich_failed", stub_url="https://example.com", reason="timeout"
    )

    row = json.loads((tmp_path / "p.events.jsonl").read_text(encoding="utf-8").strip())
    assert row["event"] == "enrich_failed"
    assert row["stub_url"] == "https://example.com"
    assert row["reason"] == "timeout"


def test_record_without_configure_is_noop(tmp_path: Path) -> None:
    parser_log.record("p", "parser_started")
    assert not (tmp_path / "p.events.jsonl").exists()


def test_record_multiple_calls_append(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record("p", "parser_started")
    parser_log.record("p", "enrich_failed", stub_url="https://a.com")

    lines = (tmp_path / "p.events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "parser_started"
    assert json.loads(lines[1])["event"] == "enrich_failed"


# ---------------------------------------------------------------------------
# record_traceback
# ---------------------------------------------------------------------------


def test_record_traceback_writes_to_run_log_with_header(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record_traceback(
        "p", "Traceback (most recent call last):\n  File ...\nValueError: oops\n"
    )

    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "=== p" in content
    assert "traceback" in content
    assert "Traceback (most recent call last):" in content
    assert "ValueError: oops" in content


def test_record_traceback_without_configure_is_noop(tmp_path: Path) -> None:
    parser_log.record_traceback("p", "some traceback")
    assert not (tmp_path / "run.log").exists()


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


def test_summarize_writes_summary_trailer(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    started = datetime(2026, 5, 12, 15, 30, 0, tzinfo=timezone.utc)
    parser_log.summarize("p", {"discovered": 12, "duration": 47.3}, started)

    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "SUMMARY OF SESSION 2026-05-12T15:30:00Z" in content
    assert "discovered=12" in content
    assert "duration=47.3" in content


def test_summarize_without_events_produces_valid_trailer(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    started = datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
    parser_log.summarize("p", {"discovered": 0, "duration": 0.0}, started)

    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "SUMMARY OF SESSION" in content
    assert "discovered=0" in content


def test_summarize_without_configure_is_noop(tmp_path: Path) -> None:
    started = datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
    parser_log.summarize("p", {"discovered": 0}, started)
    assert not (tmp_path / "run.log").exists()


def test_two_sessions_produce_two_summary_blocks(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    started1 = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)
    started2 = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)

    # Session 1
    parser_log.record("p", "parser started")
    parser_log.summarize("p", {"discovered": 5, "duration": 30.0}, started1)

    # Session 2
    parser_log.record("p", "parser started")
    parser_log.summarize("p", {"discovered": 3, "duration": 20.0}, started2)

    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert content.count("SUMMARY OF SESSION") == 2
    assert "2026-05-12T10:00:00Z" in content
    assert "2026-05-12T14:00:00Z" in content

    # Two SUMMARY blocks must be separated (not immediately adjacent)
    first_summary = content.index("SUMMARY OF SESSION 2026-05-12T10:00:00Z")
    second_summary = content.index("SUMMARY OF SESSION 2026-05-12T14:00:00Z")
    between = content[first_summary:second_summary]
    assert "\n\n" in between, (
        "SUMMARY blocks must be separated by at least one blank line"
    )


# ---------------------------------------------------------------------------
# __main__ startup
# ---------------------------------------------------------------------------


def test_main_materialises_logs_next_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from application_pipeline.orchestrator import RunSummary

    config_dir = tmp_path / "mydata"
    config_dir.mkdir()
    config_path = config_dir / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["app", str(config_path)])
    monkeypatch.setattr(
        "application_pipeline.__main__.run",
        lambda *_a, **_kw: RunSummary(),
    )

    from application_pipeline.__main__ import main

    main()

    assert (config_dir / "logs").is_dir()


def test_main_logs_land_next_to_config_regardless_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from application_pipeline.orchestrator import RunSummary

    config_dir = tmp_path / "synched"
    config_dir.mkdir()
    config_path = config_dir / "config.toml"
    config_path.write_text("", encoding="utf-8")

    other_cwd = tmp_path / "workdir"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    monkeypatch.setattr("sys.argv", ["app", str(config_path)])
    monkeypatch.setattr(
        "application_pipeline.__main__.run",
        lambda *_a, **_kw: RunSummary(),
    )

    from application_pipeline.__main__ import main

    main()

    assert (config_dir / "logs").is_dir()
    assert not (other_cwd / "synched").exists(), (
        "logs must not be created relative to cwd"
    )


# ---------------------------------------------------------------------------
# RunLog class (direct construction)
# ---------------------------------------------------------------------------


def test_runlog_event_creates_events_jsonl(tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    log = RunLog(tmp_path)
    log.event("myparser", "parser_started")

    events_file = tmp_path / "myparser.events.jsonl"
    assert events_file.exists()
    row = json.loads(events_file.read_text(encoding="utf-8").strip())
    assert _ISO8601_RE.match(row["ts"])
    assert row["event"] == "parser_started"


def test_runlog_event_appends_key_value_fields(tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    log = RunLog(tmp_path)
    log.event("p", "enrich_failed", stub_url="https://example.com", reason="timeout")

    row = json.loads((tmp_path / "p.events.jsonl").read_text(encoding="utf-8").strip())
    assert row["event"] == "enrich_failed"
    assert row["stub_url"] == "https://example.com"
    assert row["reason"] == "timeout"


def test_runlog_lifecycle_creates_lifecycle_jsonl(tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    log = RunLog(tmp_path)
    log.lifecycle("comp", "phase_changed", phase="running")

    lifecycle_file = tmp_path / "lifecycle.jsonl"
    assert lifecycle_file.exists()
    row = json.loads(lifecycle_file.read_text(encoding="utf-8").strip())
    assert _ISO8601_RE.match(row["ts"])
    assert row["event"] == "phase_changed"
    assert row["component"] == "comp"
    assert row["phase"] == "running"


def test_runlog_transcript_creates_transcripts_jsonl(tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    log = RunLog(tmp_path)
    entry = {"role": "user", "content": "hello"}
    log.transcript("agent", entry)

    transcript_file = tmp_path / "agent.transcripts.jsonl"
    assert transcript_file.exists()
    row = json.loads(transcript_file.read_text(encoding="utf-8").strip())
    assert row["role"] == "user"
    assert row["content"] == "hello"


def test_runlog_traceback_writes_to_run_log_with_header(tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    log = RunLog(tmp_path)
    log.traceback(
        "p", "Traceback (most recent call last):\n  File ...\nValueError: oops\n"
    )

    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "=== p" in content
    assert "traceback" in content
    assert "Traceback (most recent call last):" in content
    assert "ValueError: oops" in content


def test_runlog_summary_writes_summary_trailer(tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    log = RunLog(tmp_path)
    started = datetime(2026, 5, 12, 15, 30, 0, tzinfo=timezone.utc)
    log.summary("p", {"discovered": 12, "duration": 47.3}, started)

    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "SUMMARY OF SESSION 2026-05-12T15:30:00Z" in content
    assert "discovered=12" in content
    assert "duration=47.3" in content


def test_runlog_mkdir_parents_on_construction(tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    nested = tmp_path / "a" / "b" / "c"
    assert not nested.exists()
    RunLog(nested)
    assert nested.is_dir()


def test_runlog_byte_identical_to_free_functions(tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    dir_cls = tmp_path / "cls"
    dir_fn = tmp_path / "fn"

    log = RunLog(dir_cls)
    log.event("p", "parser_started", x=1)
    log.lifecycle("p", "phase_changed", phase="running")
    log.transcript("p", {"role": "user", "content": "hi"})

    parser_log.configure(dir_fn)
    parser_log.record("p", "parser_started", x=1)
    parser_log.record_lifecycle("p", "phase_changed", phase="running")
    parser_log.record_transcript("p", {"role": "user", "content": "hi"})

    for filename in ["p.events.jsonl", "lifecycle.jsonl", "p.transcripts.jsonl"]:
        rows_cls = [
            json.loads(line)
            for line in (dir_cls / filename).read_text(encoding="utf-8").splitlines()
        ]
        rows_fn = [
            json.loads(line)
            for line in (dir_fn / filename).read_text(encoding="utf-8").splitlines()
        ]
        assert len(rows_cls) == len(rows_fn)
        for cls_row, fn_row in zip(rows_cls, rows_fn):
            assert set(cls_row.keys()) == set(fn_row.keys()), (
                f"key mismatch in {filename}"
            )
            for k in cls_row:
                if k != "ts":
                    assert cls_row[k] == fn_row[k], (
                        f"value mismatch for {k} in {filename}"
                    )


def test_main_logs_land_next_to_config_when_path_is_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from application_pipeline.orchestrator import RunSummary

    config_dir = tmp_path / "synched"
    config_dir.mkdir()
    config_path = config_dir / "config.toml"
    config_path.write_text("", encoding="utf-8")

    other_cwd = tmp_path / "workdir"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    rel_path = os.path.relpath(str(config_path), str(other_cwd))
    monkeypatch.setattr("sys.argv", ["app", rel_path])
    monkeypatch.setattr(
        "application_pipeline.__main__.run",
        lambda *_a, **_kw: RunSummary(),
    )

    from application_pipeline.__main__ import main

    main()

    assert (config_dir / "logs").is_dir()
    assert not (other_cwd / "synched").exists(), (
        "logs must not be created relative to cwd"
    )
