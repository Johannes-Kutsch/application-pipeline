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


def test_root_run_log_exceeding_10000_lines_is_truncated_to_last_10000(
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


def test_root_run_log_at_or_below_10000_lines_is_unchanged(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    log_file = logs_dir / "run.log"
    content = "line1\nline2\nline3\n"
    log_file.write_text(content)

    run_maintenance(logs_dir, failures_dir)

    assert log_file.read_text() == content


def test_nested_log_artifact_at_or_below_10000_lines_is_unchanged(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    log_file = logs_dir / "parser" / "component.events.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    content = b'{"line": 1}\r\n{"line": 2}\r\n'
    log_file.write_bytes(content)

    run_maintenance(logs_dir, failures_dir)

    assert log_file.read_bytes() == content


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


def _make_evidence_file(parent: Path, name: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    evidence_file = parent / name
    evidence_file.write_text("log payload\n")
    return evidence_file


def _make_evidence_dir(parent: Path, name: str) -> Path:
    evidence_dir = parent / name
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "prompt").write_text("the prompt\n")
    (evidence_dir / "response").write_text("the response\n")
    (evidence_dir / "events").write_text("events\n")
    (evidence_dir / "meta").write_text("meta\n")
    return evidence_dir


def _age(path: Path, days: float) -> None:
    old_mtime = time.time() - days * 24 * 3600
    os.utime(path, (old_mtime, old_mtime))


def test_agent_runtime_classify_evidence_dir_older_than_30_days_is_deleted(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    evidence_dir = _make_evidence_file(
        logs_dir / "llm" / "agent-runtime" / "classify", "llm-classify-old.log"
    )
    _age(evidence_dir, 31)

    run_maintenance(logs_dir, failures_dir)

    assert not evidence_dir.exists()


def test_agent_runtime_judge_evidence_dir_older_than_30_days_is_deleted_legacy(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    evidence_file = _make_evidence_file(
        logs_dir / "llm" / "agent-runtime" / "judge", "llm-judge-old.log"
    )
    _age(evidence_file, 31)

    run_maintenance(logs_dir, failures_dir)

    assert not evidence_file.exists()


def test_agent_runtime_judge_evidence_dir_older_than_30_days_is_deleted(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    evidence_dir = _make_evidence_dir(
        logs_dir / "llm" / "agent-runtime" / "judge", "llm-judge-old"
    )
    _age(evidence_dir, 31)

    run_maintenance(logs_dir, failures_dir)

    assert not evidence_dir.exists()


def test_agent_runtime_evidence_dir_newer_than_30_days_is_preserved(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    evidence_dir = _make_evidence_dir(
        logs_dir / "llm" / "agent-runtime" / "classify", "llm-classify-new"
    )

    run_maintenance(logs_dir, failures_dir)

    assert evidence_dir.exists()
    assert (evidence_dir / "response").read_text() == "the response\n"


def test_agent_runtime_evidence_files_are_not_tail_truncated(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    parent = logs_dir / "llm" / "agent-runtime" / "classify"
    evidence_dir = parent / "llm-classify-big"
    evidence_dir.mkdir(parents=True)
    lines = [f"line {i}" for i in range(15_000)]
    (evidence_dir / "response").write_text("\n".join(lines) + "\n")

    run_maintenance(logs_dir, failures_dir)

    result_lines = (evidence_dir / "response").read_text().splitlines()
    assert len(result_lines) == 15_000


def test_agent_runtime_evidence_dir_at_30_day_cutoff_is_preserved(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir, failures_dir = dirs
    evidence_dir = _make_evidence_dir(
        logs_dir / "llm" / "agent-runtime" / "classify", "llm-classify-cutoff"
    )
    fake_now = 1_000_000_000.0
    cutoff_mtime = fake_now - 30 * 24 * 3600
    os.utime(evidence_dir, (cutoff_mtime, cutoff_mtime))
    monkeypatch.setattr(time, "time", lambda: fake_now)

    run_maintenance(logs_dir, failures_dir)

    assert evidence_dir.exists()


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


def test_filesystem_error_on_one_nested_log_artifact_does_not_stop_other_truncation(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir, failures_dir = dirs
    bad_log = logs_dir / "parser" / "bad.events.jsonl"
    good_log = logs_dir / "parser" / "good.events.jsonl"
    bad_log.parent.mkdir(parents=True, exist_ok=True)
    bad_log.write_text('{"line": 0}\n')
    good_log.write_text("\n".join(f'{{"line": {i}}}' for i in range(15_000)) + "\n")

    original_is_file = Path.is_file

    def flaky_is_file(path: Path) -> bool:
        if path == bad_log:
            raise OSError("simulated stat failure")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", flaky_is_file)

    run_maintenance(logs_dir, failures_dir)

    result_lines = good_log.read_text().splitlines()
    assert len(result_lines) == 10_000
    assert result_lines[0] == '{"line": 5000}'
    assert result_lines[-1] == '{"line": 14999}'


def test_filesystem_error_on_one_agent_runtime_evidence_dir_does_not_stop_other_maintenance(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir, failures_dir = dirs
    bad_dir = _make_evidence_dir(
        logs_dir / "llm" / "agent-runtime" / "classify", "llm-classify-bad"
    )
    _age(bad_dir, 31)
    good_log = logs_dir / "run.log"
    good_log.write_text("\n".join(f"line {i}" for i in range(15_000)) + "\n")

    original_getmtime = os.path.getmtime

    def flaky_getmtime(path: os.PathLike[str] | str) -> float:
        if Path(path) == bad_dir:
            raise OSError("simulated stat failure")
        return original_getmtime(path)

    monkeypatch.setattr(os.path, "getmtime", flaky_getmtime)

    run_maintenance(logs_dir, failures_dir)

    result_lines = good_log.read_text().splitlines()
    assert len(result_lines) == 10_000
    assert result_lines[0] == "line 5000"
    assert result_lines[-1] == "line 14999"


def test_old_failure_report_markdown_in_failures_dir_is_deleted(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    old_file = failures_dir / "2024-01-01T000000.md"
    old_file.write_text("# failure\n")
    old_mtime = time.time() - 31 * 24 * 3600
    os.utime(old_file, (old_mtime, old_mtime))

    run_maintenance(logs_dir, failures_dir)

    assert not old_file.exists()


def test_recent_failure_report_markdown_in_failures_dir_survives(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    recent_file = failures_dir / "recent.md"
    recent_file.write_text("# failure\n")

    run_maintenance(logs_dir, failures_dir)

    assert recent_file.exists()


def test_nested_failure_report_markdown_is_outside_cleanup_behavior(
    dirs: tuple[Path, Path],
) -> None:
    logs_dir, failures_dir = dirs
    nested_file = failures_dir / "nested" / "old.md"
    nested_file.parent.mkdir(parents=True, exist_ok=True)
    nested_file.write_text("# failure\n")
    old_mtime = time.time() - 31 * 24 * 3600
    os.utime(nested_file, (old_mtime, old_mtime))

    run_maintenance(logs_dir, failures_dir)

    assert nested_file.exists()


def test_maintenance_completes_silently_when_dirs_do_not_exist(
    tmp_path: Path,
) -> None:
    run_maintenance(tmp_path / "nonexistent_logs", tmp_path / "nonexistent_failures")
