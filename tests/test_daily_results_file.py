from __future__ import annotations

from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

import pytest

from application_pipeline.daily_results_file import DailyResultsFile
from application_pipeline.results.errors import ResultsFileError


class _CardKwargs(TypedDict):
    rank: int
    header: str
    summary: str
    url: str
    body: str


_FULL_CARD_KWARGS: _CardKwargs = dict(
    rank=1,
    header="Senior Engineer\nAcme · Berlin · On-site\n2026-01-01 · Senior · €80k",
    summary="A strong fit for the role.",
    url="https://example.com/job/123",
    body="Full job description here.",
)

_FULL_CARD_BYTES = (
    "# **1:** Senior Engineer\n"
    "\n"
    "Acme · Berlin · On-site\n"
    "2026-01-01 · Senior · €80k\n"
    "https://example.com/job/123\n"
    "\n"
    "A strong fit for the role.\n"
    "\n"
    "---\n"
    "\n"
    "Full job description here.\n"
    "\n"
    "---\n"
).encode("utf-8")


def test_canonical_card_bytes(tmp_path: Path) -> None:
    results_file = DailyResultsFile(tmp_path / "results.md")
    results_file.ensure_initialized()
    results_file.commit(**_FULL_CARD_KWARGS)
    assert (tmp_path / "results.md").read_bytes() == _FULL_CARD_BYTES


def test_two_commits_concatenated(tmp_path: Path) -> None:
    results_file = DailyResultsFile(tmp_path / "results.md")
    results_file.ensure_initialized()
    results_file.commit(**_FULL_CARD_KWARGS)
    results_file.commit(**_FULL_CARD_KWARGS)
    assert (tmp_path / "results.md").read_bytes() == _FULL_CARD_BYTES + _FULL_CARD_BYTES


def test_ensure_initialized_creates_nested_parent(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "results.md"
    results_file = DailyResultsFile(nested)
    results_file.ensure_initialized()
    assert nested.parent.is_dir()


def test_ensure_initialized_is_idempotent(tmp_path: Path) -> None:
    results_file = DailyResultsFile(tmp_path / "results.md")
    results_file.ensure_initialized()
    results_file.ensure_initialized()
    assert tmp_path.is_dir()


def test_ensure_initialized_does_not_create_file(tmp_path: Path) -> None:
    results_file = DailyResultsFile(tmp_path / "results.md")
    results_file.ensure_initialized()
    assert not (tmp_path / "results.md").exists()


def test_mkdir_failure_raises_results_file_error(tmp_path: Path) -> None:
    results_file = DailyResultsFile(tmp_path / "results.md")
    with patch.object(Path, "mkdir", side_effect=OSError("permission denied")):
        with pytest.raises(ResultsFileError):
            results_file.ensure_initialized()


def test_append_failure_raises_results_file_error(tmp_path: Path) -> None:
    results_file = DailyResultsFile(tmp_path / "results.md")
    results_file.ensure_initialized()
    with patch("builtins.open", side_effect=OSError("disk full")):
        with pytest.raises(ResultsFileError):
            results_file.commit(**_FULL_CARD_KWARGS)
