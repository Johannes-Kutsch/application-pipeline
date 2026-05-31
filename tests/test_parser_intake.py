from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from application_pipeline.content_gate import ContentSnapshot
from application_pipeline.extracts.card_store import CardExtract
from application_pipeline.parsers import PositionStub
from tests.parser_intake_harness import (
    CountingPreFilter,
    FailOnPostEnrichFreshnessGate,
    HarnessSeedHelper,
    ParserIntakeHarness,
    PassThroughPreFilter,
    UnexpectedContentGate,
    UnexpectedEnrichParser,
)


@pytest.mark.parametrize(
    ("dedup_kind", "seed_listing"),
    [
        (
            "url_hit",
            ParserIntakeHarness.seed_post_discover_url_hit_listing,
        ),
        (
            "tuple_hit",
            ParserIntakeHarness.seed_post_discover_tuple_hit_listing,
        ),
        (
            "fuzzy_hit",
            ParserIntakeHarness.seed_post_discover_fuzzy_hit_listing,
        ),
        (
            "run_hit",
            ParserIntakeHarness.seed_post_discover_run_hit_listing,
        ),
    ],
)
def test_post_discover_dedup_skips_stop_before_prefilter_and_enrich_and_keep_store_state(
    tmp_path: Path,
    dedup_kind: str,
    seed_listing: HarnessSeedHelper,
) -> None:
    stub = PositionStub(
        url="https://example.com/alias",
        title="Lead Platform Backend Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )

    prefilter = CountingPreFilter()
    harness = ParserIntakeHarness.create(
        tmp_path,
        parser_id="test",
        discovered_stub=stub,
        enriched_stub=stub,
        parser=UnexpectedEnrichParser(),
        domain_pre_filter=prefilter,
    )

    with harness.run_scope():
        expected_listing_id = seed_listing(harness)
        harness.process_one_position_stub(stub)

    harness.assert_dedup_recorded(dedup_kind)
    assert harness.status_display_row_body("parser test gates") == "1 dedup"
    assert prefilter.calls == 0
    assert harness.card_content(expected_listing_id) is None


def test_post_discover_prefilter_drop_persists_out_of_domain_before_enrich_and_keeps_listing_id(
    tmp_path: Path,
) -> None:
    stub = PositionStub(
        url="https://example.com/blacklisted",
        title="Senior Python Developer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )
    harness = ParserIntakeHarness.create(
        tmp_path,
        parser_id="test",
        discovered_stub=stub,
        enriched_stub=stub,
        parser=UnexpectedEnrichParser(),
        negative_keywords=["python"],
    )

    harness.process_one_position_stub(stub)

    listing_id = harness.listing_id_for_url(stub.url)
    assert listing_id == 1
    harness.assert_dedup_recorded("miss")
    assert harness.status_display_row_body("parser test gates") == "1 pre-filter"
    assert harness.card_content(listing_id) is None
    assert harness.classify_handoffs() == []
    assert harness.pool_admissions() == []

    dedup_record = harness.dedup_observation(listing_id)
    assert dedup_record is not None
    assert dedup_record.status == "out_of_domain"
    assert dedup_record.urls == (stub.url,)

    transcript_rows = harness.log_artifact_transcript_rows("pipeline_prefilter")
    assert transcript_rows == [
        {
            "url": stub.url,
            "title": stub.title,
            "source": stub.source,
            "passes": False,
            "reason": "blacklist_drop",
            "blacklist_matches": [{"term": "python"}],
            "title_len": len(stub.title or ""),
        }
    ]


def test_discover_freshness_drop_marks_matched_alias_expired_before_downstream_steps(
    tmp_path: Path,
) -> None:
    canonical_url = "https://example.com/original"
    alias_url = "https://example.com/alias"
    alias_stub = PositionStub(
        url=alias_url,
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 4, 1),
    )
    harness = ParserIntakeHarness.create(
        tmp_path,
        parser_id="test",
        discovered_stub=alias_stub,
        enriched_stub=PositionStub(
            url=canonical_url,
            title="Platform Engineer",
            source="test",
            company="Acme",
            location="Hamburg",
            posted_date=date(2026, 5, 29),
        ),
        parser=UnexpectedEnrichParser(),
    )
    listing_id = harness.seed_matched_pool_reentry_listing()
    harness.seed_persisted_card(
        listing_id,
        header="Platform Engineer\nAcme · Hamburg\n2026-01-01 · Senior",
        summary="Persisted summary",
        body="Persisted body",
    )

    harness.process_one_position_stub(alias_stub)

    assert harness.status_display_row_body("parser test gates") == "1 freshness"
    harness.assert_dedup_recorded(None)

    dedup_record = harness.dedup_observation(listing_id)
    assert dedup_record is not None
    assert dedup_record.status == "expired"
    assert dedup_record.urls == (alias_url, canonical_url)

    assert harness.card_content(listing_id) is None
    assert harness.classify_handoffs() == []
    assert harness.pool_admissions() == []

    transcript_rows = harness.log_artifact_transcript_rows("pipeline_freshness")
    assert transcript_rows == [
        {
            "url": alias_url,
            "title": "Platform Engineer",
            "source": "test",
            "posted_date": "2026-04-01",
            "deadline": None,
            "anchored_today": "2026-05-30",
            "age_days": 59,
            "passes": False,
            "reason": "too_old",
            "gate_arm": "discover",
        }
    ]


def test_post_discover_judge_pending_routes_to_pool_with_original_stub_and_keeps_card(
    tmp_path: Path,
) -> None:
    canonical_url = "https://example.com/original"
    rediscovered_stub = PositionStub(
        url="https://example.com/alias",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )
    harness = ParserIntakeHarness.create(
        parser_id="test",
        tmp_path=tmp_path,
        discovered_stub=rediscovered_stub,
    )
    listing_id = harness.seed_judge_pending_listing(
        PositionStub(
            url=canonical_url,
            title="Platform Engineer",
            source="test",
            company="Acme",
            location="Hamburg",
        ),
        card=CardExtract(
            header="Platform Engineer\nAcme · Hamburg\n2026-01-01 · Senior",
            summary="Persisted summary",
            body="Persisted body",
        ),
    )

    harness.process_one_position_stub(rediscovered_stub)

    admissions = harness.pool_admissions()
    assert len(admissions) == 1
    assert admissions[0].listing_id == listing_id
    assert admissions[0].stub == rediscovered_stub
    harness.assert_dedup_recorded("judge_pending")
    assert (
        harness.status_display_row_body("parser test") == "0 discovered · 0 forwarded"
    )

    card = harness.card_content(listing_id)
    assert card is not None
    assert card.header == "Platform Engineer\nAcme · Hamburg\n2026-01-01 · Senior"
    assert card.summary == "Persisted summary"
    assert card.body == "Persisted body"


def test_accepted_listing_delivered_to_classify_sink_with_enriched_data(
    tmp_path: Path,
) -> None:
    harness = ParserIntakeHarness.create(tmp_path)

    harness.process_one_position_stub()

    handoffs = harness.classify_handoffs()
    assert len(handoffs) == 1
    assert handoffs[0].listing_id == 1
    assert handoffs[0].stub == harness.default_enriched_stub
    assert handoffs[0].body == harness.default_body


def test_fresh_stub_reaching_classify_updates_queue_and_metrics(tmp_path: Path) -> None:
    harness = ParserIntakeHarness.create(tmp_path)

    harness.process_one_position_stub()

    harness.assert_dedup_recorded("run_hit")
    assert (
        harness.status_display_row_body("parser parser test")
        == "0 discovered · 1 forwarded"
    )


@pytest.mark.parametrize(
    ("dedup_kind", "seed_listing", "enriched_stub"),
    [
        (
            "url_hit",
            ParserIntakeHarness.seed_post_enrich_url_hit_listing,
            PositionStub(
                url="https://example.com/canonical-url-hit",
                title="Original title",
                source="test",
                company="Acme",
                location="Hamburg",
                posted_date=date(2026, 5, 29),
            ),
        ),
        (
            "tuple_hit",
            ParserIntakeHarness.seed_post_enrich_tuple_hit_listing,
            PositionStub(
                url="https://example.com/post-enrich-alias",
                title="Platform Engineer",
                source="test",
                company="Acme",
                location="Hamburg",
                posted_date=date(2026, 5, 29),
            ),
        ),
        (
            "fuzzy_hit",
            ParserIntakeHarness.seed_post_enrich_fuzzy_hit_listing,
            PositionStub(
                url="https://example.com/post-enrich-alias",
                title="Lead Platform Backend Engineer",
                source="test",
                company="Acme",
                location="Hamburg",
                posted_date=date(2026, 5, 29),
            ),
        ),
    ],
)
def test_post_enrich_non_pending_dedup_drop_stops_before_late_gates_and_keeps_card(
    tmp_path: Path,
    dedup_kind: str,
    seed_listing: HarnessSeedHelper,
    enriched_stub: PositionStub,
) -> None:
    discovered_stub = PositionStub(
        url="https://example.com/post-enrich-alias",
        title="Discovered title",
        source="test",
        posted_date=date(2026, 5, 29),
    )
    harness = ParserIntakeHarness.create(
        tmp_path,
        parser_id="test",
        discovered_stub=discovered_stub,
        enriched_stub=enriched_stub,
        body="Backfilled body " + "x" * 120,
        freshness_gate=FailOnPostEnrichFreshnessGate(),
        content_gate=UnexpectedContentGate(),
        domain_pre_filter=PassThroughPreFilter(),
    )

    expected_listing_id = seed_listing(harness)
    harness.seed_persisted_card(expected_listing_id, body="Persisted body")

    harness.process_one_position_stub(discovered_stub)
    harness.assert_dedup_recorded(dedup_kind)

    card = harness.card_content(expected_listing_id)
    assert card is not None
    assert card.body == "Persisted body"
    assert harness.classify_handoffs() == []
    assert harness.pool_admissions() == []


def test_post_enrich_judge_pending_admits_to_pool_with_enriched_stub_and_refreshes_body(
    tmp_path: Path,
) -> None:
    fresh_body = "Fresh raw description " + "x" * 120
    harness = ParserIntakeHarness.create_post_enrich_alias(
        tmp_path,
        parser_id="test",
    )
    enriched_stub = harness.set_post_enrich_alias_result(body=fresh_body)
    listing_id = harness.seed_post_enrich_judge_pending_listing(
        card=CardExtract(
            header="Persisted header",
            summary="Persisted summary",
            body="Persisted body",
        ),
    )

    harness.process_one_position_stub()

    admissions = harness.pool_admissions()
    assert len(admissions) == 1
    assert admissions[0].listing_id == listing_id
    assert admissions[0].stub == enriched_stub
    harness.assert_dedup_recorded("judge_pending")
    assert (
        harness.status_display_row_body("parser test") == "0 discovered · 0 forwarded"
    )

    assert harness.card_content(listing_id) == CardExtract(
        header="Persisted header",
        summary="Persisted summary",
        body=fresh_body,
    )


def test_post_enrich_judge_pending_without_existing_card_does_not_synthesize_extract(
    tmp_path: Path,
) -> None:
    harness = ParserIntakeHarness.create_post_enrich_alias(
        tmp_path,
        parser_id="test",
    )
    enriched_stub = harness.set_post_enrich_alias_result(
        body="Fresh raw description " + "x" * 120,
    )
    listing_id = harness.seed_post_enrich_judge_pending_listing()

    harness.process_one_position_stub()

    admissions = harness.pool_admissions()
    assert len(admissions) == 1
    assert admissions[0].listing_id == listing_id
    assert admissions[0].stub == enriched_stub
    harness.assert_dedup_recorded("judge_pending")
    assert (
        harness.status_display_row_body("parser test") == "0 discovered · 0 forwarded"
    )
    assert harness.card_content(listing_id) is None


@pytest.mark.parametrize(
    "body",
    [
        "   \n\t  ",
        "x" * 99,
    ],
)
def test_post_enrich_judge_pending_content_drop_stops_before_pool_and_preserves_card(
    tmp_path: Path,
    body: str,
) -> None:
    harness = ParserIntakeHarness.create_post_enrich_alias(
        tmp_path,
        parser_id="test",
    )
    harness.set_post_enrich_alias_result(body=body)
    listing_id = harness.seed_post_enrich_judge_pending_listing(
        card=CardExtract(
            header="Persisted header",
            summary="Persisted summary",
            body="Persisted body",
        ),
    )

    harness.process_one_position_stub()

    harness.assert_dedup_recorded("judge_pending")
    assert harness.status_display_row_body("parser test gates") == "1 content"
    assert harness.pool_admissions() == []
    assert harness.card_content(listing_id) == CardExtract(
        header="Persisted header",
        summary="Persisted summary",
        body="Persisted body",
    )
    assert harness.log_artifact_transcript_rows("pipeline_content") == [
        {
            "url": "https://example.com/post-enrich-alias",
            "title": "Platform Engineer",
            "source": "test",
            "passes": False,
            "reason": "empty_body" if not body.strip() else "too_short",
            "body_len": len(body),
        }
    ]


@pytest.mark.parametrize(
    ("posted_date", "deadline", "reason", "age_days", "deadline_text"),
    [
        (date(2026, 4, 1), None, "too_old", 59, None),
        (None, date(2026, 5, 29), "deadline_passed", None, "2026-05-29"),
    ],
)
def test_post_enrich_judge_pending_backfilled_freshness_drop_expires_matched_record_before_pool_and_content(
    tmp_path: Path,
    posted_date: date | None,
    deadline: date | None,
    reason: str,
    age_days: int | None,
    deadline_text: str | None,
) -> None:
    harness = ParserIntakeHarness.create_post_enrich_alias(
        tmp_path,
        parser_id="test",
        content_gate=UnexpectedContentGate(),
        discovered_posted_date=None,
    )
    harness.set_post_enrich_alias_result(
        body="Fresh raw description " + "x" * 120,
        posted_date=posted_date,
        deadline=deadline,
    )
    listing_id = harness.seed_post_enrich_judge_pending_listing(
        card=CardExtract(
            header="Persisted header",
            summary="Persisted summary",
            body="Persisted body",
        ),
    )

    harness.process_one_position_stub()

    harness.assert_dedup_recorded("judge_pending")
    assert harness.status_display_row_body("parser test gates") == "1 freshness"
    assert harness.card_content(listing_id) is None

    dedup_record = harness.dedup_observation(listing_id)
    assert dedup_record is not None
    assert dedup_record.status == "expired"
    assert dedup_record.urls == (
        "https://example.com/post-enrich-alias",
        "https://example.com/original-tuple",
    )

    assert harness.log_artifact_transcript_rows("pipeline_freshness") == [
        {
            "url": "https://example.com/post-enrich-alias",
            "title": "Platform Engineer",
            "source": "test",
            "posted_date": posted_date.isoformat() if posted_date is not None else None,
            "deadline": deadline_text,
            "anchored_today": "2026-05-30",
            "age_days": age_days,
            "passes": False,
            "reason": reason,
            "gate_arm": "post_enrich",
        }
    ]


def test_parser_enrich_failed_logs_and_updates_metrics_without_seen_or_card_write(
    tmp_path: Path,
) -> None:
    stub = PositionStub(
        url="https://example.com/enrich-failed",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )
    harness = ParserIntakeHarness.create(
        tmp_path,
        parser_id="test",
        discovered_stub=stub,
        domain_pre_filter=PassThroughPreFilter(),
    )
    harness.set_parser_enrich_failed_error()

    harness.process_one_position_stub(stub)

    listing_id = 1
    harness.assert_dedup_recorded("miss")
    assert harness.status_display_row_body("parser test") == (
        "0 discovered · 1 enrich_failed · 0 forwarded"
    )
    assert [
        {k: v for k, v in row.items() if k != "ts"}
        for row in harness.log_artifact_event_rows("pipeline_orchestrator")
    ] == [
        {
            "url": stub.url,
            "source": stub.source,
            "event": "enrich_failed",
        }
    ]
    assert harness.card_content(listing_id) is None
    assert harness.persisted_card_content(listing_id) is None
    assert harness.dedup_observation(listing_id) is None
    assert harness.persisted_listing_id_for_url(stub.url) is None
    assert harness.persisted_dedup_observation(listing_id) is None
    assert harness.persisted_dedup_status(listing_id) is None


@pytest.mark.parametrize(
    ("url", "body", "reason"),
    [
        ("https://example.com/empty-body", "   \n\t  ", "empty_body"),
        ("https://example.com/too-short-body", "x" * 99, "too_short"),
    ],
)
def test_post_enrich_content_drop_uses_harness_observations_without_persisting_seen_or_card(
    tmp_path: Path,
    url: str,
    body: str,
    reason: str,
) -> None:
    stub = PositionStub(
        url=url,
        title="Platform Engineer",
        source="test",
        posted_date=date(2026, 5, 29),
    )
    harness = ParserIntakeHarness.create(
        tmp_path,
        parser_id="test",
        discovered_stub=stub,
        domain_pre_filter=PassThroughPreFilter(),
    )
    harness.set_parser_enrich_result(
        stub=PositionStub(
            url=stub.url,
            title=stub.title,
            source=stub.source,
            company="Acme",
            location="Hamburg",
            posted_date=stub.posted_date,
        ),
        body=body,
    )

    harness.process_one_position_stub(stub)

    listing_id = 1
    assert harness.dedup_counter_snapshot().dedup_run_hits == 1
    assert harness.status_display_row_body("parser test gates") == "1 content"
    assert harness.classify_handoffs() == []
    assert harness.pool_admissions() == []
    assert harness.card_content(listing_id) is None
    assert harness.persisted_card_content(listing_id) is None
    assert harness.dedup_observation(listing_id) is None
    assert harness.persisted_listing_id_for_url(stub.url) is None
    assert harness.persisted_dedup_observation(listing_id) is None
    assert harness.persisted_dedup_status(listing_id) is None
    assert harness.content_snapshot() == ContentSnapshot(
        content_considered=1,
        content_dropped_empty_body=1 if reason == "empty_body" else 0,
        content_dropped_too_short=1 if reason == "too_short" else 0,
    )
    assert harness.log_artifact_transcript_rows("pipeline_content") == [
        {
            "url": stub.url,
            "title": stub.title,
            "source": stub.source,
            "passes": False,
            "reason": reason,
            "body_len": len(body),
        }
    ]


def test_parser_oversized_body_logs_skip_without_seen_or_card_write(
    tmp_path: Path,
) -> None:
    stub = PositionStub(
        url="https://example.com/oversized",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )
    harness = ParserIntakeHarness.create(
        tmp_path,
        parser_id="test",
        discovered_stub=stub,
        domain_pre_filter=PassThroughPreFilter(),
    )
    harness.set_parser_oversized_body_error()

    harness.process_one_position_stub(stub)

    listing_id = 1
    harness.assert_dedup_recorded("miss")
    assert harness.status_display_row_body("parser test") == (
        "0 discovered · 0 forwarded"
    )
    assert [
        {k: v for k, v in row.items() if k != "ts"}
        for row in harness.log_artifact_event_rows("llm_enricher")
    ] == [
        {
            "url": stub.url,
            "source": stub.source,
            "body_len": 4321,
            "event": "body_oversized",
        }
    ]
    assert harness.card_content(listing_id) is None
    assert harness.persisted_card_content(listing_id) is None
    assert harness.dedup_observation(listing_id) is None
    assert harness.persisted_listing_id_for_url(stub.url) is None
    assert harness.persisted_dedup_observation(listing_id) is None
    assert harness.persisted_dedup_status(listing_id) is None


def test_parser_transient_http_error_logs_skip_without_seen_or_card_write(
    tmp_path: Path,
) -> None:
    stub = PositionStub(
        url="https://example.com/transient",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )
    harness = ParserIntakeHarness.create(
        tmp_path,
        parser_id="test",
        discovered_stub=stub,
        domain_pre_filter=PassThroughPreFilter(),
    )
    harness.set_parser_transient_http_error()

    harness.process_one_position_stub(stub)

    listing_id = 1
    harness.assert_dedup_recorded("miss")
    assert harness.status_display_row_body("parser test") == (
        "0 discovered · 0 forwarded"
    )
    assert [
        {k: v for k, v in row.items() if k != "ts"}
        for row in harness.log_artifact_event_rows("llm_enricher")
    ] == [
        {
            "url": stub.url,
            "source": stub.source,
            "error": "503 Service Unavailable",
            "event": "fetch_transient_error",
        }
    ]
    assert harness.card_content(listing_id) is None
    assert harness.persisted_card_content(listing_id) is None
    assert harness.dedup_observation(listing_id) is None
    assert harness.persisted_listing_id_for_url(stub.url) is None
    assert harness.persisted_dedup_observation(listing_id) is None
    assert harness.persisted_dedup_status(listing_id) is None
