import json
from pathlib import Path

from application_pipeline.dedup import load as load_dedup
from application_pipeline.extracts import CardExtract, load_card_store
from application_pipeline.llm import JudgeCandidate
from application_pipeline.parsers import PositionStub
from application_pipeline.pool import Pool


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
