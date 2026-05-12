from __future__ import annotations

import re
from pathlib import Path

import pytest

import application_pipeline.debug_log as debug_log
import application_pipeline.parser_log as parser_log

_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture(autouse=True)
def reset_debug_log():
    """Reset module state before each test."""
    debug_log._logs_dir = None
    parser_log._logs_dir = None
    yield
    debug_log._logs_dir = None
    parser_log._logs_dir = None


def test_append_after_configure_creates_log_with_timestamp(tmp_path: Path) -> None:
    debug_log.configure(tmp_path)
    debug_log.append("foo", "msg")

    log_file = tmp_path / "foo.log"
    assert log_file.exists()
    line = log_file.read_text(encoding="utf-8")
    assert line.endswith(" msg\n")
    ts = line.split(" ")[0]
    assert _ISO8601_RE.match(ts), f"not an ISO-8601 timestamp: {ts!r}"


def test_append_without_configure_is_silent_noop(tmp_path: Path) -> None:
    debug_log.append("foo", "msg")
    assert not (tmp_path / "foo.log").exists()


def test_two_appends_produce_two_lines(tmp_path: Path) -> None:
    debug_log.configure(tmp_path)
    debug_log.append("bar", "first")
    debug_log.append("bar", "second")

    lines = (tmp_path / "bar.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith(" first")
    assert lines[1].endswith(" second")


def test_configure_creates_directory_if_missing(tmp_path: Path) -> None:
    logs_dir = tmp_path / "nested" / "logs"
    assert not logs_dir.exists()
    debug_log.configure(logs_dir)
    assert logs_dir.is_dir()


def test_main_materialises_logs_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from application_pipeline.orchestrator import RunSummary

    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["app", str(config_path)])
    monkeypatch.setattr(
        "application_pipeline.__main__.run",
        lambda *_a, **_kw: RunSummary(),
    )

    from application_pipeline.__main__ import main

    main()

    assert (tmp_path / "synched" / "logs").is_dir()
