"""Tests for post-run maintenance: log truncation and failure cleanup."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from application_pipeline.maintenance import run_maintenance


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    failures_dir = tmp_path / "failures"
    failures_dir.mkdir()
    return logs_dir, failures_dir


def test_log_file_exceeding_10000_lines_is_truncated_to_last_10000(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    log_file = logs_dir / "run.log"
    lines = [f"line {i}" for i in range(15_000)]
    log_file.write_text("\n".join(lines) + "\n")

    run_maintenance(logs_dir, failures_dir)

    result_lines = log_file.read_text().splitlines()
    assert len(result_lines) == 10_000
    assert result_lines[0] == "line 5000"
    assert result_lines[-1] == "line 14999"


def test_log_file_at_or_below_10000_lines_is_unchanged(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    log_file = logs_dir / "run.log"
    content = "line1\nline2\nline3\n"
    log_file.write_text(content)

    run_maintenance(logs_dir, failures_dir)

    assert log_file.read_text() == content


@pytest.mark.parametrize(
    "relative_path",
    [
        Path("parser/component.events.jsonl"),
        Path("llm/component.events.jsonl"),
        Path("pipeline/component.events.jsonl"),
    ],
)
def test_nested_log_artifact_exceeding_10000_lines_is_truncated_to_last_10000(
    dirs: tuple[Path, Path], relative_path: Path
) -> None:
    logs_dir, failures_dir = dirs
    log_file = logs_dir / relative_path
    log_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"line {i}" for i in range(15_000)]
    log_file.write_text("\n".join(lines) + "\n")

    run_maintenance(logs_dir, failures_dir)

    result_lines = log_file.read_text().splitlines()
    assert len(result_lines) == 10_000
    assert result_lines[0] == "line 5000"
    assert result_lines[-1] == "line 14999"


def test_root_lifecycle_jsonl_exceeding_10000_lines_is_truncated_to_last_10000(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    log_file = logs_dir / "lifecycle.jsonl"
    lines = [f'{{"line": {i}}}' for i in range(15_000)]
    log_file.write_text("\n".join(lines) + "\n")

    run_maintenance(logs_dir, failures_dir)

    result_lines = log_file.read_text().splitlines()
    assert len(result_lines) == 10_000
    assert result_lines[0] == '{"line": 5000}'
    assert result_lines[-1] == '{"line": 14999}'


def test_flat_log_artifact_exceeding_10000_lines_is_truncated_to_last_10000(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    log_file = logs_dir / "parser_component.events.jsonl"
    lines = [f'{{"line": {i}}}' for i in range(15_000)]
    log_file.write_text("\n".join(lines) + "\n")

    run_maintenance(logs_dir, failures_dir)

    result_lines = log_file.read_text().splitlines()
    assert len(result_lines) == 10_000
    assert result_lines[0] == '{"line": 5000}'
    assert result_lines[-1] == '{"line": 14999}'


def test_old_md_files_in_failures_dir_are_deleted(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    old_file = failures_dir / "2024-01-01T000000.md"
    old_file.write_text("# failure\n")
    old_mtime = time.time() - 31 * 24 * 3600
    os.utime(old_file, (old_mtime, old_mtime))

    run_maintenance(logs_dir, failures_dir)

    assert not old_file.exists()


def test_recent_md_files_in_failures_dir_survive(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    recent_file = failures_dir / "recent.md"
    recent_file.write_text("# failure\n")

    run_maintenance(logs_dir, failures_dir)

    assert recent_file.exists()


def test_maintenance_completes_silently_when_dirs_do_not_exist(
    tmp_path: Path,
) -> None:
    run_maintenance(tmp_path / "nonexistent_logs", tmp_path / "nonexistent_failures")
