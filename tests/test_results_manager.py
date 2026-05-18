import os
import stat
from pathlib import Path

import pytest

from application_pipeline.results import (
    FILE_HEADER,
    ResultsFileError,
    append,
    ensure_initialized,
)


@pytest.fixture
def results_path(tmp_path: Path) -> Path:
    return tmp_path / "results" / "current.md"


# --- ensure_initialized ---


def test_creates_file_with_header_when_missing(results_path: Path) -> None:
    ensure_initialized(results_path)
    assert results_path.read_text(encoding="utf-8") == FILE_HEADER


def test_creates_parent_directories(results_path: Path) -> None:
    assert not results_path.parent.exists()
    ensure_initialized(results_path)
    assert results_path.parent.is_dir()


def test_overwrites_zero_byte_file(results_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_bytes(b"")
    ensure_initialized(results_path)
    assert results_path.read_text(encoding="utf-8") == FILE_HEADER


def test_no_op_on_non_empty_file(results_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    existing = "<!-- schema-version: 1 -->\n\n# Some Position\n"
    results_path.write_text(existing, encoding="utf-8")
    ensure_initialized(results_path)
    assert results_path.read_text(encoding="utf-8") == existing


def test_idempotent_three_consecutive_calls(results_path: Path) -> None:
    ensure_initialized(results_path)
    after_first = results_path.read_bytes()
    ensure_initialized(results_path)
    ensure_initialized(results_path)
    assert results_path.read_bytes() == after_first


def test_ensure_initialized_wraps_oserror_as_results_file_error(
    results_path: Path,
) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(results_path.parent, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(ResultsFileError):
            ensure_initialized(results_path)
    finally:
        os.chmod(results_path.parent, stat.S_IRWXU)


# --- append ---


def test_writes_block_verbatim(results_path: Path) -> None:
    ensure_initialized(results_path)
    block = "# Acme · Dev · Berlin\n\n## AI Assessment\n\nGreat fit.\n\n---\n<https://example.com/1>\n"
    append(results_path, block)
    assert results_path.read_text(encoding="utf-8") == FILE_HEADER + block


def test_two_appends_concatenate_without_normalization(results_path: Path) -> None:
    ensure_initialized(results_path)
    block1 = "# Card One\n"
    block2 = "# Card Two\n"
    append(results_path, block1)
    append(results_path, block2)
    assert results_path.read_text(encoding="utf-8") == FILE_HEADER + block1 + block2


def test_durable_after_append(results_path: Path) -> None:
    ensure_initialized(results_path)
    block = "# Durable Position\n"
    append(results_path, block)
    content = results_path.read_text(encoding="utf-8")
    assert block in content


def test_append_wraps_oserror_as_results_file_error(results_path: Path) -> None:
    ensure_initialized(results_path)
    os.chmod(results_path, stat.S_IRUSR)
    try:
        with pytest.raises(ResultsFileError):
            append(results_path, "# Should fail\n")
    finally:
        os.chmod(results_path, stat.S_IRUSR | stat.S_IWUSR)


# --- FILE_HEADER constant ---


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
