from __future__ import annotations

import pytest

from application_pipeline.parsers import PositionStub
from tests.parser_intake_harness import ParserIntakeHarness


@pytest.fixture
def parser_intake_harness(tmp_path):
    return ParserIntakeHarness.create(tmp_path)


def test_parser_intake_harness_observes_classify_handoff_and_deduplication_miss(
    parser_intake_harness: ParserIntakeHarness,
) -> None:
    parser_intake_harness.process_one_position_stub()

    handoffs = parser_intake_harness.classify_handoffs()

    assert len(handoffs) == 1
    assert handoffs[0].listing_id == 1
    assert handoffs[0].stub == parser_intake_harness.default_enriched_stub
    assert handoffs[0].body == parser_intake_harness.default_body
    assert parser_intake_harness.dedup_counter_snapshot().dedup_run_hits == 1
    assert (
        parser_intake_harness.status_display_row_body("parser parser test")
        == "0 discovered · 1 forwarded"
    )


def test_parser_intake_harness_seeds_out_of_domain_listing_for_deduplication_observation(
    parser_intake_harness: ParserIntakeHarness,
) -> None:
    listing_id = parser_intake_harness.seed_out_of_domain_listing()

    assert parser_intake_harness.dedup_status(listing_id) == "out_of_domain"
    assert parser_intake_harness.dedup_observation(listing_id) is not None


def test_parser_intake_harness_seeds_in_run_pending_listing_within_run_scope(
    parser_intake_harness: ParserIntakeHarness,
) -> None:
    with parser_intake_harness.run_scope():
        listing_id = parser_intake_harness.seed_in_run_pending_listing()

        parser_intake_harness.process_one_position_stub()

    assert parser_intake_harness.classify_handoffs() == []
    assert parser_intake_harness.dedup_counter_snapshot().dedup_run_hits == 1
    assert parser_intake_harness.dedup_observation(listing_id) is None


def test_parser_intake_harness_observes_pool_reentry_and_persisted_card_refresh(
    tmp_path,
) -> None:
    original_listing = PositionStub(
        url="https://example.com/original",
        title="Backend Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
    )
    alias_discovered_stub = PositionStub(
        url="https://example.com/alias",
        title="Backend Engineer",
        source="test",
    )
    alias_enriched_stub = PositionStub(
        url=alias_discovered_stub.url,
        title=alias_discovered_stub.title,
        source=alias_discovered_stub.source,
        company="Acme",
        location="Hamburg",
    )
    parser_intake_harness = ParserIntakeHarness.create(
        tmp_path,
        discovered_stub=alias_discovered_stub,
        enriched_stub=alias_enriched_stub,
        body="Updated body " + "x" * 120,
    )

    listing_id = parser_intake_harness.seed_matched_pool_reentry_listing(
        original_listing
    )
    parser_intake_harness.seed_persisted_card(
        listing_id,
        body="Persisted body",
    )

    parser_intake_harness.process_one_position_stub()

    assert parser_intake_harness.classify_handoffs() == []
    admissions = parser_intake_harness.pool_admissions()
    updated_card = parser_intake_harness.card_content(listing_id)

    assert len(admissions) == 1
    assert admissions[0].listing_id == listing_id
    assert admissions[0].stub == alias_enriched_stub
    assert updated_card is not None
    assert updated_card.body == "Updated body " + "x" * 120
    assert parser_intake_harness.dedup_counter_snapshot().judge_resumed == 1


def test_parser_intake_harness_reads_log_artifact_rows_for_events_and_transcripts(
    parser_intake_harness: ParserIntakeHarness,
) -> None:
    parser_intake_harness.run_log.event("parser_test", "skip", listing_id=7)
    parser_intake_harness.run_log.transcript(
        "pipeline_prefilter",
        {"url": "https://example.com/role", "passes": True},
    )

    event_rows = parser_intake_harness.log_artifact_event_rows("parser_test")

    assert len(event_rows) == 1
    assert event_rows[0]["event"] == "skip"
    assert event_rows[0]["listing_id"] == 7
    assert parser_intake_harness.log_artifact_transcript_rows("pipeline_prefilter") == [
        {"url": "https://example.com/role", "passes": True}
    ]
