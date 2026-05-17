import os
import stat
from pathlib import Path

import pytest

from application_pipeline.results import (
    FILE_HEADER,
    ResultsFileError,
    ResultsFileManager,
    load,
)


@pytest.fixture
def results_path(tmp_path: Path) -> Path:
    return tmp_path / "results" / "current.md"


@pytest.fixture
def manager(results_path: Path) -> ResultsFileManager:
    return ResultsFileManager(results_path)


class TestEnsureInitialized:
    def test_creates_file_with_header_when_missing(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        manager.ensure_initialized()
        assert results_path.read_text(encoding="utf-8") == FILE_HEADER

    def test_creates_parent_directories(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        assert not results_path.parent.exists()
        manager.ensure_initialized()
        assert results_path.parent.is_dir()

    def test_overwrites_zero_byte_file(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_bytes(b"")
        manager.ensure_initialized()
        assert results_path.read_text(encoding="utf-8") == FILE_HEADER

    def test_no_op_on_non_empty_file(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        existing = "<!-- schema-version: 1 -->\n\n# Some Position\n"
        results_path.write_text(existing, encoding="utf-8")
        manager.ensure_initialized()
        assert results_path.read_text(encoding="utf-8") == existing

    def test_idempotent_three_consecutive_calls(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        manager.ensure_initialized()
        after_first = results_path.read_bytes()
        manager.ensure_initialized()
        manager.ensure_initialized()
        assert results_path.read_bytes() == after_first

    def test_wraps_oserror_as_results_file_error(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(results_path.parent, stat.S_IRUSR | stat.S_IXUSR)
        try:
            with pytest.raises(ResultsFileError):
                manager.ensure_initialized()
        finally:
            os.chmod(results_path.parent, stat.S_IRWXU)


class TestAppend:
    def test_writes_block_verbatim(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        manager.ensure_initialized()
        block = "# Acme · Dev · Berlin\n\n## AI Assessment\n\nGreat fit.\n\n---\n<https://example.com/1>\n"
        manager.append(block)
        assert results_path.read_text(encoding="utf-8") == FILE_HEADER + block

    def test_two_appends_concatenate_without_normalization(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        manager.ensure_initialized()
        block1 = "# Card One\n"
        block2 = "# Card Two\n"
        manager.append(block1)
        manager.append(block2)
        assert results_path.read_text(encoding="utf-8") == FILE_HEADER + block1 + block2

    def test_durable_after_append(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        manager.ensure_initialized()
        block = "# Durable Position\n"
        manager.append(block)
        content = results_path.read_text(encoding="utf-8")
        assert block in content

    def test_wraps_oserror_as_results_file_error(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        manager.ensure_initialized()
        os.chmod(results_path, stat.S_IRUSR)
        try:
            with pytest.raises(ResultsFileError):
                manager.append("# Should fail\n")
        finally:
            os.chmod(results_path, stat.S_IRUSR | stat.S_IWUSR)


class TestFileHeader:
    def test_file_header_contains_schema_version_comment(self) -> None:
        assert "<!-- schema-version: 1 -->" in FILE_HEADER

    def test_file_header_contains_reset_hint_comment(self) -> None:
        assert (
            "<!-- Delete this file and re-run the pipeline to reset -->" in FILE_HEADER
        )

    def test_file_header_has_no_h1(self) -> None:
        assert not any(line.startswith("# ") for line in FILE_HEADER.splitlines())

    def test_file_header_ends_with_blank_line(self) -> None:
        assert FILE_HEADER.endswith("\n\n")


class TestNextPositionNumberAbsent:
    def test_next_position_number_is_not_a_method(
        self, manager: ResultsFileManager
    ) -> None:
        assert not hasattr(manager, "next_position_number")


class TestLoadFactory:
    def test_load_returns_manager(self, results_path: Path) -> None:
        mgr = load(results_path)
        assert isinstance(mgr, ResultsFileManager)

    def test_load_produces_functional_manager(self, results_path: Path) -> None:
        mgr = load(results_path)
        mgr.ensure_initialized()
        assert results_path.read_text(encoding="utf-8") == FILE_HEADER
