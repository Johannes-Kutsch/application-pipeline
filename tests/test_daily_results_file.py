from __future__ import annotations

from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

import pytest

from application_pipeline.daily_results_file import DailyResultsFile
from application_pipeline.daily_results_file import ResultsFileError


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


def _split_cards(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    card_start_indexes = [
        index for index, line in enumerate(lines) if line.startswith("# **")
    ]
    return [
        "".join(lines[start:end])
        for start, end in zip(card_start_indexes, [*card_start_indexes[1:], len(lines)])
    ]


def _assert_card_semantics(card: str, *, expected: _CardKwargs) -> None:
    header_lines = expected["header"].splitlines()
    title = header_lines[0]
    meaningful_lines = [line for line in card.splitlines() if line]

    assert meaningful_lines == [
        f"# **{expected['rank']}:** {title}",
        *header_lines[1:],
        expected["url"],
        expected["summary"],
        "---",
        expected["body"],
        "---",
    ]


@pytest.mark.parametrize(
    "relative_path",
    [Path("results.md"), Path("results/2026-01-01.md")],
    ids=["non_dated_file", "dated_file"],
)
def test_committed_card_preserves_card_semantics(
    tmp_path: Path, relative_path: Path
) -> None:
    results_path = tmp_path / relative_path
    results_file = DailyResultsFile(results_path)
    results_file.ensure_initialized()
    results_file.commit(**_FULL_CARD_KWARGS)
    content = results_path.read_text(encoding="utf-8")
    cards = _split_cards(content)
    assert len(cards) == 1
    _assert_card_semantics(cards[0], expected=_FULL_CARD_KWARGS)


def test_two_commits_append_in_rank_order(tmp_path: Path) -> None:
    results_file = DailyResultsFile(tmp_path / "results" / "2026-01-01.md")
    results_file.ensure_initialized()
    results_file.commit(**_FULL_CARD_KWARGS)
    second_card: _CardKwargs = dict(
        rank=2,
        header="Staff Engineer\nAcme · Remote\n2026-01-02 · Staff",
        summary="A second strong fit for the role.",
        url="https://example.com/job/456",
        body="Another full job description here.",
    )
    results_file.commit(**second_card)

    content = (tmp_path / "results" / "2026-01-01.md").read_text(encoding="utf-8")
    cards = _split_cards(content)
    assert len(cards) == 2
    _assert_card_semantics(cards[0], expected=_FULL_CARD_KWARGS)
    _assert_card_semantics(cards[1], expected=second_card)
    assert content.index("# **1:**") < content.index("# **2:**")


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
