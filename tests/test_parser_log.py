from __future__ import annotations

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
    parser_log.record("myparser", "parser started")

    log_file = tmp_path / "myparser.log"
    assert log_file.exists()
    line = log_file.read_text(encoding="utf-8").strip()
    assert _ISO8601_RE.match(line)
    assert line.endswith("parser started")


def test_record_appends_key_value_fields(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record(
        "p", "enrich_failed", stub_url="https://example.com", reason="timeout"
    )

    content = (tmp_path / "p.log").read_text(encoding="utf-8")
    assert "enrich_failed" in content
    assert "stub_url=https://example.com" in content
    assert "reason=timeout" in content


def test_record_without_configure_is_noop(tmp_path: Path) -> None:
    parser_log.record("p", "parser started")
    assert not (tmp_path / "p.log").exists()


def test_record_multiple_calls_append(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record("p", "parser started")
    parser_log.record("p", "enrich_failed", stub_url="https://a.com")

    lines = (tmp_path / "p.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "parser started" in lines[0]
    assert "enrich_failed" in lines[1]


# ---------------------------------------------------------------------------
# record_traceback
# ---------------------------------------------------------------------------


def test_record_traceback_writes_timestamped_first_line(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    parser_log.record_traceback(
        "p", "Traceback (most recent call last):\n  File ...\nValueError: oops\n"
    )

    content = (tmp_path / "p.log").read_text(encoding="utf-8")
    lines = content.splitlines()
    assert _ISO8601_RE.match(lines[0])
    assert "traceback" in lines[0]
    assert "Traceback (most recent call last):" in content
    assert "ValueError: oops" in content


def test_record_traceback_without_configure_is_noop(tmp_path: Path) -> None:
    parser_log.record_traceback("p", "some traceback")
    assert not (tmp_path / "p.log").exists()


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


def test_summarize_writes_summary_trailer(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    started = datetime(2026, 5, 12, 15, 30, 0, tzinfo=timezone.utc)
    parser_log.summarize("p", {"discovered": 12, "duration": 47.3}, started)

    content = (tmp_path / "p.log").read_text(encoding="utf-8")
    assert "SUMMARY OF SESSION 2026-05-12T15:30:00Z" in content
    assert "discovered=12" in content
    assert "duration=47.3" in content


def test_summarize_without_events_produces_valid_trailer(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    started = datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
    parser_log.summarize("p", {"discovered": 0, "duration": 0.0}, started)

    content = (tmp_path / "p.log").read_text(encoding="utf-8")
    assert "SUMMARY OF SESSION" in content
    assert "discovered=0" in content


def test_summarize_without_configure_is_noop(tmp_path: Path) -> None:
    started = datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
    parser_log.summarize("p", {"discovered": 0}, started)
    assert not (tmp_path / "p.log").exists()


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

    content = (tmp_path / "p.log").read_text(encoding="utf-8")
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
