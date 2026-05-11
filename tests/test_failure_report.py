from __future__ import annotations

import importlib.metadata
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from application_pipeline.failure_report import write_failure


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    return tmp_path / "results"


_FIXED_TIME = datetime(2026, 5, 11, 16, 4, 0, tzinfo=UTC)
_FIXED_TIMESTAMP = "2026-05-11T16:04:00Z"
_FIXED_FILENAME = "2026-05-11T16-04-00Z"


def _write_at(timestamp: datetime, *args, **kwargs) -> Path:
    with patch("application_pipeline.failure_report.datetime") as mock_dt:
        mock_dt.now.return_value = timestamp
        return write_failure(*args, **kwargs)


class TestFileCreation:
    def test_returns_path_inside_failures_subdir_with_iso_timestamp(
        self, results_dir: Path
    ) -> None:
        path = _write_at(
            _FIXED_TIME, "parser:test", ValueError("boom"), "log", results_dir
        )
        assert path == results_dir / "failures" / f"{_FIXED_FILENAME}.md"
        assert path.exists()

    def test_creates_failures_subdirectory_when_missing(
        self, results_dir: Path
    ) -> None:
        failures_dir = results_dir / "failures"
        assert not failures_dir.exists()
        write_failure("stage", ValueError("e"), "tail", results_dir)
        assert failures_dir.is_dir()

    def test_no_tmp_file_left_after_write(self, results_dir: Path) -> None:
        path = _write_at(_FIXED_TIME, "stage", RuntimeError("e"), "tail", results_dir)
        assert not Path(str(path) + ".tmp").exists()


class TestMarkdownBody:
    def test_body_contains_stage(self, results_dir: Path) -> None:
        path = write_failure(
            "orchestrator:init", RuntimeError("oops"), "tail", results_dir
        )
        assert "orchestrator:init" in path.read_text(encoding="utf-8")

    def test_body_contains_error_class(self, results_dir: Path) -> None:
        path = write_failure("stage", ValueError("bad input"), "tail", results_dir)
        assert "ValueError" in path.read_text(encoding="utf-8")

    def test_body_contains_error_message(self, results_dir: Path) -> None:
        path = write_failure("stage", ValueError("bad input"), "tail", results_dir)
        assert "bad input" in path.read_text(encoding="utf-8")

    def test_body_contains_log_tail(self, results_dir: Path) -> None:
        log = "line A\nline B\nline C"
        path = write_failure("stage", RuntimeError("e"), log, results_dir)
        body = path.read_text(encoding="utf-8")
        assert "line A" in body
        assert "line B" in body
        assert "line C" in body

    def test_heading_contains_timestamp(self, results_dir: Path) -> None:
        path = _write_at(_FIXED_TIME, "stage", RuntimeError("e"), "tail", results_dir)
        assert _FIXED_TIMESTAMP in path.read_text(encoding="utf-8")

    def test_heading_contains_tag_when_discoverable(self, results_dir: Path) -> None:
        with patch.object(importlib.metadata, "version", return_value="v1.2.3"):
            path = write_failure("stage", RuntimeError("e"), "tail", results_dir)
        assert "v1.2.3" in path.read_text(encoding="utf-8")

    def test_heading_omits_tag_when_package_not_found(self, results_dir: Path) -> None:
        with patch.object(
            importlib.metadata,
            "version",
            side_effect=importlib.metadata.PackageNotFoundError,
        ):
            path = write_failure("stage", RuntimeError("e"), "tail", results_dir)
        body = path.read_text(encoding="utf-8")
        assert body.startswith("# Run failed at")
        assert "(tag" not in body


class TestNoClobber:
    def test_two_writes_at_different_timestamps_produce_separate_files(
        self, results_dir: Path
    ) -> None:
        t1 = datetime(2026, 5, 11, 16, 4, 0, tzinfo=UTC)
        t2 = datetime(2026, 5, 11, 16, 4, 1, tzinfo=UTC)
        path1 = _write_at(t1, "stage", RuntimeError("e1"), "tail", results_dir)
        path2 = _write_at(t2, "stage", RuntimeError("e2"), "tail", results_dir)
        assert path1 != path2
        assert path1.exists()
        assert path2.exists()
