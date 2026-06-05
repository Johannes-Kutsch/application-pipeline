import json
from pathlib import Path

import pytest

from application_pipeline.dedup import load as load_dedup
from application_pipeline.extracts import CardExtract, load_card_store
from application_pipeline.llm import JudgeCandidate, MatchVerdict
from application_pipeline.parsers import PositionStub
from application_pipeline.pool import Pool


class _RecordingDailyResultsFile:
    def __init__(self) -> None:
        self.commits: list[dict[str, str | int]] = []

    def commit(
        self, *, rank: int, header: str, summary: str, url: str, body: str
    ) -> None:
        self.commits.append(
            {
                "rank": rank,
                "header": header,
                "summary": summary,
                "url": url,
                "body": body,
            }
        )


class _RecordingSelectedByJudgeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def mark_selected_by_judge(
        self, key_or_listing_id: int, stub: PositionStub | None = None
    ) -> None:
        assert stub is not None
        self.calls.append((key_or_listing_id, stub.url))


class _BoomResultsFile:
    def commit(
        self, *, rank: int, header: str, summary: str, url: str, body: str
    ) -> None:
        raise RuntimeError("disk full")


def test_pool_projects_judge_candidates_from_admitted_listings(
    tmp_path: Path,
) -> None:
    card_store = load_card_store(tmp_path / "extracts.json")
    pool = Pool()

    matched_stub = PositionStub(
        url="https://example.com/matched",
        title="Matched role",
        source="test",
    )
    judge_pending_stub = PositionStub(
        url="https://example.com/judge-pending",
        title="Judge pending role",
        source="test",
    )
    missing_card_stub = PositionStub(
        url="https://example.com/missing-card",
        title="Missing card role",
        source="test",
    )

    pool.add_matched(matched_stub, listing_id=11)
    pool.add_judge_pending(judge_pending_stub, listing_id=12)
    pool.add_matched(missing_card_stub, listing_id=13)

    card_store.put(11, CardExtract(header="Header 11", summary="Summary 11"))
    card_store.put(12, CardExtract(header="Header 12", summary="Summary 12"))

    assert pool.pool_size == 3
    assert pool.judge_candidates(card_store) == [
        JudgeCandidate(id=11, header="Header 11", summary="Summary 11"),
        JudgeCandidate(id=12, header="Header 12", summary="Summary 12"),
    ]


def test_pool_completes_judge_selection_without_exposing_stub_storage(
    tmp_path: Path,
) -> None:
    pool = Pool()
    stub = PositionStub(
        url="https://example.com/selected",
        title="Selected role",
        source="test",
        company="Acme",
        location="Hamburg",
    )
    dedup_store = load_dedup(tmp_path / ".seen.json")

    pool.add_matched(stub, listing_id=21)
    dedup_store.mark_matched(21, stub)

    assert pool.selected_listing_url(21) == "https://example.com/selected"

    pool.mark_selected_by_judge(dedup_store, 21)

    on_disk = json.loads((tmp_path / ".seen.json").read_text(encoding="utf-8"))
    assert on_disk["21"]["status"] == "selected_by_judge"


def test_pool_applies_match_verdicts_in_rank_order(tmp_path: Path) -> None:
    card_store = load_card_store(tmp_path / "extracts.json")
    pool = Pool()
    results_file = _RecordingDailyResultsFile()
    dedup_store = _RecordingSelectedByJudgeStore()

    first_stub = PositionStub(
        url="https://example.com/first",
        title="First role",
        source="test",
    )
    second_stub = PositionStub(
        url="https://example.com/second",
        title="Second role",
        source="test",
    )
    pool.add_matched(first_stub, listing_id=101)
    pool.add_judge_pending(second_stub, listing_id=202)
    card_store.put(
        101,
        CardExtract(
            header="Header 101",
            summary="Summary 101",
            body="Raw description 101",
        ),
    )
    card_store.put(
        202,
        CardExtract(
            header="Header 202",
            summary="Summary 202",
            body="Raw description 202",
        ),
    )

    written = pool.apply_match_verdicts(
        [MatchVerdict(id=101, rank=2), MatchVerdict(id=202, rank=1)],
        card_store=card_store,
        daily_results_file=results_file,
        dedup_store=dedup_store,
    )

    assert written == 2
    assert results_file.commits == [
        {
            "rank": 1,
            "header": "Header 202",
            "summary": "Summary 202",
            "url": "https://example.com/second",
            "body": "Raw description 202",
        },
        {
            "rank": 2,
            "header": "Header 101",
            "summary": "Summary 101",
            "url": "https://example.com/first",
            "body": "Raw description 101",
        },
    ]
    assert dedup_store.calls == [
        (202, "https://example.com/second"),
        (101, "https://example.com/first"),
    ]


def test_pool_does_not_transition_winner_when_commit_fails(tmp_path: Path) -> None:
    card_store = load_card_store(tmp_path / "extracts.json")
    pool = Pool()
    dedup_store = _RecordingSelectedByJudgeStore()

    stub = PositionStub(
        url="https://example.com/failing",
        title="Failing role",
        source="test",
    )
    pool.add_matched(stub, listing_id=303)
    card_store.put(
        303,
        CardExtract(
            header="Header 303",
            summary="Summary 303",
            body="Raw description 303",
        ),
    )

    with pytest.raises(RuntimeError, match="disk full"):
        pool.apply_match_verdicts(
            [MatchVerdict(id=303, rank=1)],
            card_store=card_store,
            daily_results_file=_BoomResultsFile(),
            dedup_store=dedup_store,
        )

    assert dedup_store.calls == []


def test_pool_skips_verdicts_without_cards(tmp_path: Path) -> None:
    card_store = load_card_store(tmp_path / "extracts.json")
    pool = Pool()
    results_file = _RecordingDailyResultsFile()
    dedup_store = _RecordingSelectedByJudgeStore()

    stub = PositionStub(
        url="https://example.com/no-card",
        title="No card role",
        source="test",
    )
    pool.add_matched(stub, listing_id=404)

    written = pool.apply_match_verdicts(
        [MatchVerdict(id=404, rank=1)],
        card_store=card_store,
        daily_results_file=results_file,
        dedup_store=dedup_store,
    )

    assert written == 0
    assert results_file.commits == []
    assert dedup_store.calls == []


def test_pool_commits_empty_url_when_stub_is_missing(tmp_path: Path) -> None:
    card_store = load_card_store(tmp_path / "extracts.json")
    pool = Pool()
    results_file = _RecordingDailyResultsFile()
    dedup_store = _RecordingSelectedByJudgeStore()

    card_store.put(
        505,
        CardExtract(
            header="Header 505",
            summary="Summary 505",
            body="Raw description 505",
        ),
    )

    written = pool.apply_match_verdicts(
        [MatchVerdict(id=505, rank=1)],
        card_store=card_store,
        daily_results_file=results_file,
        dedup_store=dedup_store,
    )

    assert written == 1
    assert results_file.commits == [
        {
            "rank": 1,
            "header": "Header 505",
            "summary": "Summary 505",
            "url": "",
            "body": "Raw description 505",
        }
    ]
    assert dedup_store.calls == []
