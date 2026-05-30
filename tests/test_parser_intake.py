from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import load as dedup_load
from application_pipeline.extracts import load_card_store
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.parser_intake import Dropped, ParserIntake, PoolAdmitted
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import PositionStub
from application_pipeline.parsers.types import EnrichResult
from application_pipeline.prefilter_gate import PreFilterGate


_DedupSeeder = Callable[[Any, PositionStub], int]


class _UnexpectedEnrichParser:
    def __enter__(self) -> "_UnexpectedEnrichParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        raise AssertionError("discover-arm freshness drop must stop before enrich()")


class _PassThroughPreFilter:
    def admit(self, stub: PositionStub) -> bool:
        return True


class _CountingPreFilter:
    def __init__(self) -> None:
        self.calls = 0

    def admit(self, stub: PositionStub) -> bool:
        self.calls += 1
        return True


def _seed_url_hit(dedup_store: Any, stub: PositionStub) -> int:
    dedup_store.mark_out_of_domain(stub)
    return dedup_store.listing_id_for(stub.url)


def _seed_tuple_hit(dedup_store: Any, stub: PositionStub) -> int:
    original = PositionStub(
        url="https://example.com/original",
        title=stub.title,
        source=stub.source,
        company=stub.company,
        location=stub.location,
    )
    dedup_store.mark_out_of_domain(original)
    return dedup_store.listing_id_for(original.url)


def _seed_fuzzy_hit(dedup_store: Any, stub: PositionStub) -> int:
    original = PositionStub(
        url="https://example.com/original",
        title="Senior Lead Platform Backend Engineer",
        source=stub.source,
        company=stub.company,
        location=stub.location,
    )
    dedup_store.mark_out_of_domain(original)
    return dedup_store.listing_id_for(original.url)


def _seed_run_hit(dedup_store: Any, stub: PositionStub) -> int:
    return dedup_store.is_seen(stub).listing_id


@pytest.mark.parametrize(
    ("dedup_kind", "prepare"),
    [
        (
            "url_hit",
            _seed_url_hit,
        ),
        (
            "tuple_hit",
            _seed_tuple_hit,
        ),
        (
            "fuzzy_hit",
            _seed_fuzzy_hit,
        ),
        (
            "run_hit",
            _seed_run_hit,
        ),
    ],
)
def test_post_discover_dedup_skips_stop_before_prefilter_and_enrich_and_keep_listing_id(
    tmp_path: Path,
    dedup_kind: str,
    prepare: _DedupSeeder,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    stub = PositionStub(
        url="https://example.com/alias",
        title="Lead Platform Backend Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    prefilter = _CountingPreFilter()
    run_log = RunLog(logs_dir)
    intake = ParserIntake(
        parser=_UnexpectedEnrichParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        domain_pre_filter=prefilter,
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
    )

    with dedup_store.run_scope():
        expected_listing_id = prepare(dedup_store, stub)
        outcome = intake.process_position_stub(stub)

    assert isinstance(outcome, Dropped)
    assert outcome.reason == f"dedup_{dedup_kind}"
    assert outcome.dedup_kind == dedup_kind
    assert outcome.dedup_events == (dedup_kind,)
    assert outcome.listing_id == expected_listing_id
    assert outcome.parser_row_metric == "dedup_dropped"
    assert prefilter.calls == 0
    assert card_store.get(expected_listing_id) is None


def test_post_discover_prefilter_drop_persists_out_of_domain_before_enrich_and_keeps_listing_id(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    stub = PositionStub(
        url="https://example.com/blacklisted",
        title="Senior Python Developer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    display = FakeStatusDisplay()
    run_log = RunLog(logs_dir)
    intake = ParserIntake(
        parser=_UnexpectedEnrichParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=display,
            run_log=run_log,
        ),
        deduplication=dedup_store,
        domain_pre_filter=PreFilterGate(
            blacklist=["python"],
            dedup=dedup_store,
            display=display,
            run_log=run_log,
        ),
        content_gate=ContentGate(display=display, run_log=run_log),
        card_store=card_store,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(stub)

    assert isinstance(outcome, Dropped)
    assert outcome.reason == "prefilter"
    assert outcome.listing_id == 1
    assert outcome.dedup_kind is None
    assert outcome.dedup_events == ("miss",)
    assert outcome.parser_row_metric == "prefilter_dropped"
    assert card_store.get(1) is None

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data["1"]["status"] == "out_of_domain"
    assert seen_data["1"]["urls"] == [stub.url]

    transcript_rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "prefilter.transcripts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
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
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    canonical_url = "https://example.com/original"
    alias_url = "https://example.com/alias"

    seen_path.write_text(
        json.dumps(
            {
                "7": {
                    "urls": [canonical_url],
                    "company_lc": "acme",
                    "title_lc": "platform engineer",
                    "location_lc": "hamburg",
                    "status": "matched",
                    "status_last_changed": "2026-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    extracts_path.write_text(
        json.dumps(
            {
                "7": {
                    "header": "Platform Engineer\nAcme · Hamburg\n2026-01-01 · Senior",
                    "summary": "Persisted summary",
                    "body": "Persisted body",
                }
            }
        ),
        encoding="utf-8",
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    run_log = RunLog(logs_dir)
    display = FakeStatusDisplay()
    freshness_gate = FreshnessGate(
        anchored_today=date(2026, 5, 30),
        max_listing_age_days=30,
        dedup=dedup_store,
        display=display,
        run_log=run_log,
    )
    intake = ParserIntake(
        parser=_UnexpectedEnrichParser(),
        freshness_gate=freshness_gate,
        deduplication=dedup_store,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=display, run_log=run_log),
        card_store=card_store,
    )

    outcome = intake.process_position_stub(
        PositionStub(
            url=alias_url,
            title="Platform Engineer",
            source="test",
            company="Acme",
            location="Hamburg",
            posted_date=date(2026, 4, 1),
        )
    )

    assert isinstance(outcome, Dropped)
    assert outcome.reason == "freshness_discover"
    assert outcome.parser_row_metric == "freshness_dropped"

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data["7"]["status"] == "expired"
    assert seen_data["7"]["urls"] == [alias_url, canonical_url]

    assert card_store.get(7) is None

    transcript_rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "freshness.transcripts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
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
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    canonical_url = "https://example.com/original"
    rediscovered_stub = PositionStub(
        url="https://example.com/alias",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )

    seen_path.write_text(
        json.dumps(
            {
                "7": {
                    "urls": [canonical_url],
                    "company_lc": "acme",
                    "title_lc": "platform engineer",
                    "location_lc": "hamburg",
                    "status": "matched",
                    "status_last_changed": "2026-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    extracts_path.write_text(
        json.dumps(
            {
                "7": {
                    "header": "Platform Engineer\nAcme · Hamburg\n2026-01-01 · Senior",
                    "summary": "Persisted summary",
                    "body": "Persisted body",
                }
            }
        ),
        encoding="utf-8",
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    run_log = RunLog(logs_dir)
    display = FakeStatusDisplay()
    intake = ParserIntake(
        parser=_UnexpectedEnrichParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=display,
            run_log=run_log,
        ),
        deduplication=dedup_store,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=display, run_log=run_log),
        card_store=card_store,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(rediscovered_stub)

    assert isinstance(outcome, PoolAdmitted)
    assert outcome.pool_admission.listing_id == 7
    assert outcome.pool_admission.stub == rediscovered_stub
    assert outcome.dedup_kind == "judge_pending"
    assert outcome.dedup_events == ("judge_pending",)
    assert outcome.parser_row_metric is None

    card = card_store.get(7)
    assert card is not None
    assert card.header == "Platform Engineer\nAcme · Hamburg\n2026-01-01 · Senior"
    assert card.summary == "Persisted summary"
    assert card.body == "Persisted body"
