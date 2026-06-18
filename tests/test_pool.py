import json
from pathlib import Path
from unittest.mock import patch

import pytest

from application_pipeline.dedup import load as load_dedup
from application_pipeline.daily_results_file import ResultsFileError
from application_pipeline.daily_results_file import DailyResultsFile
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


def test_pool_exposes_judge_candidates_as_the_candidate_projection_operation() -> None:
    pool = Pool()

    assert not hasattr(pool, "build_candidates")
    assert not hasattr(pool, "selected_listing_url")
    assert not hasattr(pool, "mark_selected_by_judge")


def test_pool_completes_judge_selection_without_exposing_stub_storage(
    tmp_path: Path,
) -> None:
    pool = Pool()
    card_store = load_card_store(tmp_path / "extracts.json")
    results_file = _RecordingDailyResultsFile()
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
    card_store.put(
        21,
        CardExtract(
            header="Header 21",
            summary="Summary 21",
            body="Raw description 21",
        ),
    )

    written = pool.apply_match_verdicts(
        [MatchVerdict(id=21, rank=1)],
        card_store=card_store,
        daily_results_file=results_file,
        dedup_store=dedup_store,
    )

    assert written == 1
    assert results_file.commits == [
        {
            "rank": 1,
            "header": "Header 21",
            "summary": "Summary 21",
            "url": "https://example.com/selected",
            "body": "Raw description 21",
        }
    ]
    on_disk = json.loads((tmp_path / ".seen.json").read_text(encoding="utf-8"))
    assert on_disk["21"]["status"] == "selected_by_judge"


def test_pool_applies_match_verdicts_through_real_local_collaborators(
    tmp_path: Path,
) -> None:
    pool = Pool()
    card_store = load_card_store(tmp_path / "extracts.json")
    dedup_store = load_dedup(tmp_path / ".seen.json", card_store=card_store)
    daily_results_file = DailyResultsFile(tmp_path / "results" / "2026-06-17.md")
    daily_results_file.ensure_initialized()
    stub = PositionStub(
        url="https://example.com/selected",
        title="Selected role",
        source="test",
        company="Acme",
        location="Hamburg",
    )

    pool.add_matched(stub, listing_id=22)
    dedup_store.mark_matched(22, stub)
    card_store.put(
        22,
        CardExtract(
            header="Header 22\nAcme · Hamburg",
            summary="Summary 22",
            body="Raw description 22",
        ),
    )

    written = pool.apply_match_verdicts(
        [MatchVerdict(id=22, rank=1)],
        card_store=card_store,
        daily_results_file=daily_results_file,
        dedup_store=dedup_store,
    )

    assert written == 1
    assert (tmp_path / "results" / "2026-06-17.md").read_text(encoding="utf-8") == (
        "# **1:** Header 22\n"
        "\n"
        "Acme · Hamburg\n"
        "https://example.com/selected\n"
        "\n"
        "Summary 22\n"
        "\n"
        "---\n"
        "\n"
        "Raw description 22\n"
        "\n"
        "---\n"
    )
    on_disk = json.loads((tmp_path / ".seen.json").read_text(encoding="utf-8"))
    assert on_disk["22"]["status"] == "selected_by_judge"
    assert card_store.get(22) is None


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


def test_pool_propagates_daily_results_commit_failure_without_selecting_or_deleting_card(
    tmp_path: Path,
) -> None:
    pool = Pool()
    card_store = load_card_store(tmp_path / "extracts.json")
    dedup_store = load_dedup(tmp_path / ".seen.json", card_store=card_store)
    daily_results_file = DailyResultsFile(tmp_path / "results" / "2026-06-17.md")
    daily_results_file.ensure_initialized()
    stub = PositionStub(
        url="https://example.com/failing-real",
        title="Failing real role",
        source="test",
    )

    pool.add_matched(stub, listing_id=313)
    dedup_store.mark_matched(313, stub)
    card_store.put(
        313,
        CardExtract(
            header="Header 313",
            summary="Summary 313",
            body="Raw description 313",
        ),
    )

    with patch("builtins.open", side_effect=OSError("disk full")):
        with pytest.raises(ResultsFileError, match="append failed"):
            pool.apply_match_verdicts(
                [MatchVerdict(id=313, rank=1)],
                card_store=card_store,
                daily_results_file=daily_results_file,
                dedup_store=dedup_store,
            )

    on_disk = json.loads((tmp_path / ".seen.json").read_text(encoding="utf-8"))
    assert on_disk["313"]["status"] == "matched"
    assert card_store.get(313) == CardExtract(
        header="Header 313",
        summary="Summary 313",
        body="Raw description 313",
    )


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


def test_pool_uses_latest_admitted_stub_when_applying_match_verdicts(
    tmp_path: Path,
) -> None:
    card_store = load_card_store(tmp_path / "extracts.json")
    pool = Pool()
    results_file = _RecordingDailyResultsFile()
    dedup_store = _RecordingSelectedByJudgeStore()

    original_stub = PositionStub(
        url="https://example.com/original",
        title="Original role",
        source="test",
    )
    replacement_stub = PositionStub(
        url="https://example.com/replacement",
        title="Replacement role",
        source="test",
    )
    pool.add_matched(original_stub, listing_id=606)
    pool.add_judge_pending(replacement_stub, listing_id=606)
    card_store.put(
        606,
        CardExtract(
            header="Header 606",
            summary="Summary 606",
            body="Raw description 606",
        ),
    )

    written = pool.apply_match_verdicts(
        [MatchVerdict(id=606, rank=1)],
        card_store=card_store,
        daily_results_file=results_file,
        dedup_store=dedup_store,
    )

    assert written == 1
    assert results_file.commits == [
        {
            "rank": 1,
            "header": "Header 606",
            "summary": "Summary 606",
            "url": "https://example.com/replacement",
            "body": "Raw description 606",
        }
    ]
    assert dedup_store.calls == [(606, "https://example.com/replacement")]


def test_pool_commits_fallback_url_when_stub_is_missing(tmp_path: Path) -> None:
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


def test_pool_writes_fallback_card_with_empty_url_when_stub_is_missing(
    tmp_path: Path,
) -> None:
    pool = Pool()
    card_store = load_card_store(tmp_path / "extracts.json")
    dedup_store = load_dedup(tmp_path / ".seen.json", card_store=card_store)
    daily_results_file = DailyResultsFile(tmp_path / "results" / "2026-06-17.md")
    daily_results_file.ensure_initialized()

    matched_stub = PositionStub(
        url="https://example.com/matched-without-pool-stub",
        title="Matched without pool stub role",
        source="test",
    )
    dedup_store.mark_matched(505, matched_stub)
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
        daily_results_file=daily_results_file,
        dedup_store=dedup_store,
    )

    assert written == 1
    assert (tmp_path / "results" / "2026-06-17.md").read_text(encoding="utf-8") == (
        "# **1:** Header 505\n\n\n\n\nSummary 505\n\n---\n\nRaw description 505\n\n---\n"
    )
    on_disk = json.loads((tmp_path / ".seen.json").read_text(encoding="utf-8"))
    assert on_disk["505"]["status"] == "matched"
    assert card_store.get(505) == CardExtract(
        header="Header 505",
        summary="Summary 505",
        body="Raw description 505",
    )


def test_pool_commits_cards_in_rank_order_with_empty_url_fallbacks(
    tmp_path: Path,
) -> None:
    pool = Pool()
    card_store = load_card_store(tmp_path / "extracts.json")
    dedup_store = load_dedup(tmp_path / ".seen.json", card_store=card_store)
    daily_results_file = DailyResultsFile(tmp_path / "results" / "2026-06-17.md")
    daily_results_file.ensure_initialized()

    second_rank_stub = PositionStub(
        url="https://example.com/101",
        title="Second rank role",
        source="test",
    )
    first_rank_stub = PositionStub(
        url="https://example.com/202",
        title="First rank role",
        source="test",
    )

    pool.add_matched(second_rank_stub, listing_id=101)
    pool.add_judge_pending(first_rank_stub, listing_id=202)
    dedup_store.mark_matched(101, second_rank_stub)
    dedup_store.mark_matched(202, first_rank_stub)

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
    card_store.put(
        303,
        CardExtract(
            header="Header 303",
            summary="Summary 303",
            body="Raw description 303",
        ),
    )

    written = pool.apply_match_verdicts(
        [
            MatchVerdict(id=101, rank=2),
            MatchVerdict(id=303, rank=3),
            MatchVerdict(id=202, rank=1),
        ],
        card_store=card_store,
        daily_results_file=daily_results_file,
        dedup_store=dedup_store,
    )

    assert written == 3
    assert (tmp_path / "results" / "2026-06-17.md").read_text(encoding="utf-8") == (
        "# **1:** Header 202\n"
        "\n"
        "\n"
        "https://example.com/202\n"
        "\n"
        "Summary 202\n"
        "\n"
        "---\n"
        "\n"
        "Raw description 202\n"
        "\n"
        "---\n"
        "# **2:** Header 101\n"
        "\n"
        "\n"
        "https://example.com/101\n"
        "\n"
        "Summary 101\n"
        "\n"
        "---\n"
        "\n"
        "Raw description 101\n"
        "\n"
        "---\n"
        "# **3:** Header 303\n"
        "\n"
        "\n"
        "\n"
        "\n"
        "Summary 303\n"
        "\n"
        "---\n"
        "\n"
        "Raw description 303\n"
        "\n"
        "---\n"
    )

    on_disk = json.loads((tmp_path / ".seen.json").read_text(encoding="utf-8"))
    assert on_disk["101"]["status"] == "selected_by_judge"
    assert on_disk["202"]["status"] == "selected_by_judge"
    assert "303" not in on_disk


def test_pool_skips_admitted_winner_without_card_and_preserves_dedup_status(
    tmp_path: Path,
) -> None:
    pool = Pool()
    card_store = load_card_store(tmp_path / "extracts.json")
    dedup_store = load_dedup(tmp_path / ".seen.json", card_store=card_store)
    daily_results_file = DailyResultsFile(tmp_path / "results" / "2026-06-17.md")
    daily_results_file.ensure_initialized()

    selected_stub = PositionStub(
        url="https://example.com/selected",
        title="Selected role",
        source="test",
    )
    missing_card_stub = PositionStub(
        url="https://example.com/missing-card",
        title="Missing card role",
        source="test",
    )

    pool.add_matched(selected_stub, listing_id=707)
    pool.add_matched(missing_card_stub, listing_id=808)
    dedup_store.mark_matched(707, selected_stub)
    dedup_store.mark_matched(808, missing_card_stub)

    card_store.put(
        707,
        CardExtract(
            header="Header 707",
            summary="Summary 707",
            body="Raw description 707",
        ),
    )

    written = pool.apply_match_verdicts(
        [MatchVerdict(id=808, rank=1), MatchVerdict(id=707, rank=2)],
        card_store=card_store,
        daily_results_file=daily_results_file,
        dedup_store=dedup_store,
    )

    assert written == 1
    assert (tmp_path / "results" / "2026-06-17.md").read_text(encoding="utf-8") == (
        "# **2:** Header 707\n"
        "\n"
        "\n"
        "https://example.com/selected\n"
        "\n"
        "Summary 707\n"
        "\n"
        "---\n"
        "\n"
        "Raw description 707\n"
        "\n"
        "---\n"
    )
    on_disk = json.loads((tmp_path / ".seen.json").read_text(encoding="utf-8"))
    assert on_disk["707"]["status"] == "selected_by_judge"
    assert on_disk["808"]["status"] == "matched"
