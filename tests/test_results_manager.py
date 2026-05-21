import os
import stat
import sys
from pathlib import Path

import pytest

from application_pipeline.results import (
    ResultsFileError,
    append,
    ensure_initialized,
)


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    return tmp_path / "results"


@pytest.fixture
def results_path(tmp_path: Path) -> Path:
    return tmp_path / "results" / "current.md"


# --- ensure_initialized ---


def test_creates_parent_directory_when_missing(results_dir: Path) -> None:
    assert not results_dir.exists()
    ensure_initialized(results_dir / "2026-05-19.md")
    assert results_dir.is_dir()


def test_creates_nested_parent_directories(results_path: Path) -> None:
    assert not results_path.parent.exists()
    ensure_initialized(results_path)
    assert results_path.parent.is_dir()


def test_idempotent_when_directory_already_exists(results_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    # Should not raise when directory already exists
    ensure_initialized(results_path)
    assert results_path.parent.is_dir()


def test_does_not_create_file(results_path: Path) -> None:
    ensure_initialized(results_path)
    assert not results_path.exists(), (
        "ensure_initialized must not create the file itself"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX chmod does not restrict directory creation on Windows",
)
def test_ensure_initialized_wraps_oserror_as_results_file_error(
    results_path: Path,
) -> None:
    results_path.parent.parent.mkdir(parents=True, exist_ok=True)
    # Make the parent's parent read-only so mkdir inside it fails
    os.chmod(results_path.parent.parent, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(ResultsFileError):
            ensure_initialized(results_path)
    finally:
        os.chmod(results_path.parent.parent, stat.S_IRWXU)


# --- append ---


def test_writes_block_verbatim(results_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    block = "# Acme · Dev · Berlin\n\n## AI Assessment\n\nGreat fit.\n\n---\n<https://example.com/1>\n"
    append(results_path, block)
    assert results_path.read_text(encoding="utf-8") == block


def test_two_appends_concatenate_without_normalization(results_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    block1 = "# Card One\n"
    block2 = "# Card Two\n"
    append(results_path, block1)
    append(results_path, block2)
    assert results_path.read_text(encoding="utf-8") == block1 + block2


def test_durable_after_append(results_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    block = "# Durable Position\n"
    append(results_path, block)
    content = results_path.read_text(encoding="utf-8")
    assert block in content


def test_append_wraps_oserror_as_results_file_error(results_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text("existing content\n", encoding="utf-8")
    os.chmod(results_path, stat.S_IRUSR)
    try:
        with pytest.raises(ResultsFileError):
            append(results_path, "# Should fail\n")
    finally:
        os.chmod(results_path, stat.S_IRUSR | stat.S_IWUSR)
