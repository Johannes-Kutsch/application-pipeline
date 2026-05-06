import os
import stat
from pathlib import Path

import pytest

from application_pipeline.results import ResultsFileError, ResultsFileManager, load

HEADER = "# Results\n\n"


@pytest.fixture
def results_path(tmp_path: Path) -> Path:
    return tmp_path / "results" / "current.md"


@pytest.fixture
def manager(results_path: Path) -> ResultsFileManager:
    return ResultsFileManager(results_path, HEADER)


class TestEnsureInitialized:
    def test_creates_file_with_header_when_missing(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        manager.ensure_initialized()
        assert results_path.read_text(encoding="utf-8") == HEADER

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
        assert results_path.read_text(encoding="utf-8") == HEADER

    def test_no_op_on_non_empty_file(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        existing = "# Results\n\n## 1. Some Position\n"
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


class TestNextPositionNumber:
    def test_returns_one_on_fresh_initialized_file(
        self, manager: ResultsFileManager
    ) -> None:
        manager.ensure_initialized()
        assert manager.next_position_number() == 1

    def test_returns_max_plus_one(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(HEADER + "## 47. Some Position\n", encoding="utf-8")
        assert manager.next_position_number() == 48

    def test_uses_max_not_last_when_multiple_headers(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            HEADER + "## 3. Position\n## 1. Position\n## 5. Position\n## 2. Position\n"
        )
        results_path.write_text(content, encoding="utf-8")
        assert manager.next_position_number() == 6

    def test_raises_when_file_missing(self, manager: ResultsFileManager) -> None:
        with pytest.raises(ResultsFileError):
            manager.next_position_number()

    def test_raises_when_file_zero_bytes(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_bytes(b"")
        with pytest.raises(ResultsFileError):
            manager.next_position_number()

    def test_non_empty_file_with_no_position_headers_returns_one(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(HEADER, encoding="utf-8")
        assert manager.next_position_number() == 1


class TestAppend:
    def test_writes_block_verbatim(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        manager.ensure_initialized()
        block = "## 1. Test Position\nSome content\n"
        manager.append(block)
        assert results_path.read_text(encoding="utf-8") == HEADER + block

    def test_two_appends_concatenate_without_normalization(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        manager.ensure_initialized()
        block1 = "## 1. First\n"
        block2 = "## 2. Second\n"
        manager.append(block1)
        manager.append(block2)
        assert results_path.read_text(encoding="utf-8") == HEADER + block1 + block2

    def test_durable_after_append(
        self, manager: ResultsFileManager, results_path: Path
    ) -> None:
        manager.ensure_initialized()
        block = "## 1. Durable Position\n"
        manager.append(block)
        content = results_path.read_text(encoding="utf-8")
        assert block in content

    def test_propagates_oserror(
        self, manager: ResultsFileManager, results_path: Path, tmp_path: Path
    ) -> None:
        manager.ensure_initialized()
        os.chmod(results_path, stat.S_IRUSR)
        try:
            with pytest.raises(OSError):
                manager.append("## 2. Should fail\n")
        finally:
            os.chmod(results_path, stat.S_IRUSR | stat.S_IWUSR)


class TestNumberingSurvivesFileReplacement:
    def test_numbering_restarts_after_file_moved(
        self, manager: ResultsFileManager, results_path: Path, tmp_path: Path
    ) -> None:
        manager.ensure_initialized()
        manager.append("## 1. First\n")
        manager.append("## 2. Second\n")
        manager.append("## 3. Third\n")

        results_path.rename(tmp_path / "old_current.md")

        manager.ensure_initialized()
        assert manager.next_position_number() == 1


class TestLoadFactory:
    def test_load_returns_manager(self, results_path: Path) -> None:
        mgr = load(results_path, HEADER)
        assert isinstance(mgr, ResultsFileManager)

    def test_load_produces_functional_manager(self, results_path: Path) -> None:
        mgr = load(results_path, HEADER)
        mgr.ensure_initialized()
        assert results_path.read_text(encoding="utf-8") == HEADER
