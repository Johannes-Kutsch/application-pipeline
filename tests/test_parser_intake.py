from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable, cast

import httpx
import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import load as dedup_load
from application_pipeline.dedup_counters import DedupCounters
from application_pipeline.extracts import load_card_store
from application_pipeline.extracts.card_store import CardExtract
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.parser_intake import (
    ClassifyForwarded,
    Dropped,
    OversizedBodySkip,
    ParserIntake,
    PoolAdmitted,
    RetryableEnrichFailure,
    TransientHttpSkip,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import PositionStub
from application_pipeline.parsers.body_fetch import OversizedBodyError
from application_pipeline.parsers.types import EnrichFailedError, EnrichResult
from application_pipeline.prefilter_gate import PreFilterGate
from application_pipeline.run_metrics import RunMetrics


_DedupSeeder = Callable[[Any, PositionStub], int]


def _make_dedup_counters(run_log: RunLog) -> DedupCounters:
    return DedupCounters(display=FakeStatusDisplay(), run_log=run_log)


def _make_run_metrics(
    run_log: RunLog, *, parser_id: str = "test"
) -> tuple[RunMetrics, FakeStatusDisplay]:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser(parser_id, order=0, total_queries=1, has_native_enrich=True)
    return metrics, display


def _last_body(display: FakeStatusDisplay, row: str) -> str:
    updates = display.body_updates_for(row)
    assert updates, f"no body updates for row {row!r}"
    return updates[-1]


def _assert_dedup_recorded(
    counters: DedupCounters,
    expected: str | None,
) -> None:
    snapshot = counters.snapshot()
    assert snapshot.dedup_url_hits == (1 if expected == "url_hit" else 0)
    assert snapshot.dedup_tuple_hits == (1 if expected == "tuple_hit" else 0)
    assert snapshot.dedup_fuzzy_hits == (1 if expected == "fuzzy_hit" else 0)
    assert snapshot.dedup_run_hits == (1 if expected == "run_hit" else 0)
    assert snapshot.dedup_misses == (1 if expected == "miss" else 0)
    assert snapshot.judge_resumed == (1 if expected == "judge_pending" else 0)


def _read_event_rows(
    logs_dir: Path, layer: str, component: str
) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (logs_dir / layer / f"{component}.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


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


class _FailOnPostEnrichFreshnessGate:
    def admit(
        self,
        stub: PositionStub,
        *,
        gate_arm: str,
        deadline: date | None,
    ) -> bool:
        if gate_arm == "post_enrich":
            raise AssertionError(
                "post-enrich dedup drop must stop before post-enrich freshness"
            )
        return True


class _UnexpectedContentGate:
    def admit(self, body: str, stub: PositionStub) -> bool:
        raise AssertionError("post-enrich dedup drop must stop before Content Gate")


class _EnrichFailedParser:
    def __enter__(self) -> "_EnrichFailedParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        raise EnrichFailedError("native enrich failed")


class _OversizedBodyParser:
    def __enter__(self) -> "_OversizedBodyParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        raise OversizedBodyError(url=stub.url, source=stub.source, body_len=4321)


class _TransientHttpErrorParser:
    def __enter__(self) -> "_TransientHttpErrorParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        raise httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=httpx.Request("GET", stub.url),
            response=httpx.Response(503),
        )


class _BackfillingParser:
    def __enter__(self) -> "_BackfillingParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        enriched = PositionStub(
            url=stub.url,
            title=stub.title,
            source=stub.source,
            company="Acme",
            location="Hamburg",
            posted_date=stub.posted_date,
        )
        return EnrichResult(
            stub=enriched,
            body="Fresh backend role " + "x" * 120,
            mode="native",
        )


class _BackfillingAliasParser:
    def __init__(self, *, url: str, company: str, location: str, title: str) -> None:
        self._url = url
        self._company = company
        self._location = location
        self._title = title

    def __enter__(self) -> "_BackfillingAliasParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        enriched = PositionStub(
            url=self._url,
            title=self._title,
            source=stub.source,
            company=self._company,
            location=self._location,
            posted_date=stub.posted_date,
        )
        return EnrichResult(
            stub=enriched,
            body="Backfilled body " + "x" * 120,
            mode="native",
        )


class _BackfillingMatchedParser:
    def __init__(
        self,
        *,
        url: str,
        company: str,
        location: str,
        title: str,
        body: str,
    ) -> None:
        self._url = url
        self._company = company
        self._location = location
        self._title = title
        self._body = body

    def __enter__(self) -> "_BackfillingMatchedParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        enriched = PositionStub(
            url=self._url,
            title=self._title,
            source=stub.source,
            company=self._company,
            location=self._location,
            posted_date=stub.posted_date,
        )
        return EnrichResult(stub=enriched, body=self._body, mode="native")


class _BackfillingMatchedFreshnessDropParser:
    def __init__(
        self,
        *,
        url: str,
        company: str,
        location: str,
        title: str,
        posted_date: date | None = None,
        deadline: date | None = None,
        body: str,
    ) -> None:
        self._url = url
        self._company = company
        self._location = location
        self._title = title
        self._posted_date = posted_date
        self._deadline = deadline
        self._body = body

    def __enter__(self) -> "_BackfillingMatchedFreshnessDropParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        enriched = PositionStub(
            url=self._url,
            title=self._title,
            source=stub.source,
            company=self._company,
            location=self._location,
            posted_date=self._posted_date,
            deadline=self._deadline,
        )
        return EnrichResult(stub=enriched, body=self._body, mode="native")


class _EmptyBodyParser:
    def __enter__(self) -> "_EmptyBodyParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        enriched = PositionStub(
            url=stub.url,
            title=stub.title,
            source=stub.source,
            company="Acme",
            location="Hamburg",
            posted_date=stub.posted_date,
        )
        return EnrichResult(stub=enriched, body="   \n\t  ", mode="native")


class _TooShortBodyParser:
    def __enter__(self) -> "_TooShortBodyParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        enriched = PositionStub(
            url=stub.url,
            title=stub.title,
            source=stub.source,
            company="Acme",
            location="Hamburg",
            posted_date=stub.posted_date,
        )
        return EnrichResult(stub=enriched, body="x" * 99, mode="native")


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


def _seed_post_enrich_url_hit(dedup_store: Any, stub: PositionStub) -> int:
    original = PositionStub(
        url="https://example.com/canonical-url-hit",
        title="Original title",
        source=stub.source,
        company="Acme",
        location="Hamburg",
    )
    dedup_store.mark_out_of_domain(original)
    return dedup_store.listing_id_for(original.url)


def _seed_post_enrich_tuple_hit(dedup_store: Any, stub: PositionStub) -> int:
    original = PositionStub(
        url="https://example.com/original-tuple",
        title="Platform Engineer",
        source=stub.source,
        company="Acme",
        location="Hamburg",
    )
    dedup_store.mark_out_of_domain(original)
    return dedup_store.listing_id_for(original.url)


def _seed_post_enrich_fuzzy_hit(dedup_store: Any, stub: PositionStub) -> int:
    original = PositionStub(
        url="https://example.com/original-fuzzy",
        title="Senior Lead Platform Backend Engineer",
        source=stub.source,
        company="Acme",
        location="Hamburg",
    )
    dedup_store.mark_out_of_domain(original)
    return dedup_store.listing_id_for(original.url)


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
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_UnexpectedEnrichParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=prefilter,
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        expected_listing_id = prepare(dedup_store, stub)
        outcome = intake.process_position_stub(stub)

    assert isinstance(outcome, Dropped)
    assert outcome.reason == f"dedup_{dedup_kind}"
    assert outcome.dedup_kind == dedup_kind
    _assert_dedup_recorded(dedup_counters, dedup_kind)
    assert outcome.listing_id == expected_listing_id
    assert _last_body(metrics_display, "parser test gates") == "1 dedup"
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
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_UnexpectedEnrichParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=display,
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=PreFilterGate(
            blacklist=["python"],
            dedup=dedup_store,
            display=display,
            run_log=run_log,
        ),
        content_gate=ContentGate(display=display, run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(stub)

    assert isinstance(outcome, Dropped)
    assert outcome.reason == "prefilter"
    assert outcome.listing_id == 1
    assert outcome.dedup_kind is None
    _assert_dedup_recorded(dedup_counters, "miss")
    assert _last_body(metrics_display, "parser test gates") == "1 pre-filter"
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
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_UnexpectedEnrichParser(),
        freshness_gate=freshness_gate,
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=display, run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
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
    assert _last_body(metrics_display, "parser test gates") == "1 freshness"
    _assert_dedup_recorded(dedup_counters, None)

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
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_UnexpectedEnrichParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=display,
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=display, run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(rediscovered_stub)

    assert isinstance(outcome, PoolAdmitted)
    assert outcome.pool_admission.listing_id == 7
    assert outcome.pool_admission.stub == rediscovered_stub
    assert outcome.dedup_kind == "judge_pending"
    _assert_dedup_recorded(dedup_counters, "judge_pending")
    assert _last_body(metrics_display, "parser test") == "0 discovered · 0 forwarded"

    card = card_store.get(7)
    assert card is not None
    assert card.header == "Platform Engineer\nAcme · Hamburg\n2026-01-01 · Senior"
    assert card.summary == "Persisted summary"
    assert card.body == "Persisted body"


def test_fresh_stub_reaching_classify_forwarded_keeps_parser_thread_handoff_data(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    discovered_stub = PositionStub(
        url="https://example.com/fresh-forward",
        title="Backend Engineer",
        source="test",
        posted_date=date(2026, 5, 29),
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    run_log = RunLog(logs_dir)
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log, parser_id="parser_test")
    intake = ParserIntake(
        parser_id="parser_test",
        parser=_BackfillingParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(discovered_stub)

    assert isinstance(outcome, ClassifyForwarded)
    assert outcome.listing_id == 1
    assert outcome.stub == PositionStub(
        url="https://example.com/fresh-forward",
        title="Backend Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )
    assert outcome.body == "Fresh backend role " + "x" * 120
    assert outcome.parser_id == "parser_test"
    assert outcome.enrich_mode == "native"
    assert outcome.post_enrich_dedup_kind == "run_hit"
    _assert_dedup_recorded(dedup_counters, "run_hit")
    assert (
        _last_body(metrics_display, "parser parser test")
        == "0 discovered · 1 forwarded"
    )


@pytest.mark.parametrize(
    ("dedup_kind", "prepare", "enriched_url", "enriched_title"),
    [
        (
            "url_hit",
            _seed_post_enrich_url_hit,
            "https://example.com/canonical-url-hit",
            "Original title",
        ),
        (
            "tuple_hit",
            _seed_post_enrich_tuple_hit,
            "https://example.com/post-enrich-alias",
            "Platform Engineer",
        ),
        (
            "fuzzy_hit",
            _seed_post_enrich_fuzzy_hit,
            "https://example.com/post-enrich-alias",
            "Lead Platform Backend Engineer",
        ),
    ],
)
def test_post_enrich_non_pending_dedup_drop_stops_before_late_gates_and_keeps_hit_kind(
    tmp_path: Path,
    dedup_kind: str,
    prepare: _DedupSeeder,
    enriched_url: str,
    enriched_title: str,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    stub = PositionStub(
        url="https://example.com/post-enrich-alias",
        title="Discovered title",
        source="test",
        posted_date=date(2026, 5, 29),
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    expected_listing_id = prepare(dedup_store, stub)
    card_store.put(
        expected_listing_id,
        CardExtract(
            header="Persisted header",
            summary="Persisted summary",
            body="Persisted body",
        ),
    )
    run_log = RunLog(logs_dir)
    dedup_counters = _make_dedup_counters(run_log)
    intake = ParserIntake(
        parser=_BackfillingAliasParser(
            url=enriched_url,
            company="Acme",
            location="Hamburg",
            title=enriched_title,
        ),
        freshness_gate=cast(Any, _FailOnPostEnrichFreshnessGate()),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=cast(Any, _UnexpectedContentGate()),
        card_store=card_store,
        run_log=run_log,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(stub)

    assert isinstance(outcome, Dropped)
    assert outcome.reason == f"dedup_{dedup_kind}"
    assert outcome.dedup_kind == dedup_kind
    _assert_dedup_recorded(dedup_counters, dedup_kind)
    assert outcome.listing_id == expected_listing_id
    assert outcome.stub == PositionStub(
        url=enriched_url,
        title=enriched_title,
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )

    card = card_store.get(expected_listing_id)
    assert card is not None
    assert card.body == "Persisted body"


def test_post_enrich_judge_pending_admits_to_pool_with_enriched_stub_and_refreshes_body(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    discovered_stub = PositionStub(
        url="https://example.com/post-enrich-alias",
        title="Discovered title",
        source="test",
        posted_date=date(2026, 5, 29),
    )
    fresh_body = "Fresh raw description " + "x" * 120

    original = PositionStub(
        url="https://example.com/original-tuple",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
    )
    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    dedup_store.mark_matched(original)
    listing_id = dedup_store.listing_id_for(original.url)
    assert listing_id is not None
    card_store.put(
        listing_id,
        CardExtract(
            header="Persisted header",
            summary="Persisted summary",
            body="Persisted body",
        ),
    )
    run_log = RunLog(logs_dir)
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_BackfillingMatchedParser(
            url="https://example.com/post-enrich-alias",
            company="Acme",
            location="Hamburg",
            title="Platform Engineer",
            body=fresh_body,
        ),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(discovered_stub)

    assert isinstance(outcome, PoolAdmitted)
    assert not isinstance(outcome, ClassifyForwarded)
    assert outcome.pool_admission.listing_id == listing_id
    assert outcome.pool_admission.stub == PositionStub(
        url="https://example.com/post-enrich-alias",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )
    assert outcome.dedup_kind == "judge_pending"
    _assert_dedup_recorded(dedup_counters, "judge_pending")

    assert card_store.get(listing_id) == CardExtract(
        header="Persisted header",
        summary="Persisted summary",
        body=fresh_body,
    )


def test_post_enrich_judge_pending_without_existing_card_does_not_synthesize_extract(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    discovered_stub = PositionStub(
        url="https://example.com/post-enrich-alias",
        title="Discovered title",
        source="test",
        posted_date=date(2026, 5, 29),
    )

    original = PositionStub(
        url="https://example.com/original-tuple",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
    )
    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    dedup_store.mark_matched(original)
    listing_id = dedup_store.listing_id_for(original.url)
    assert listing_id is not None
    run_log = RunLog(logs_dir)
    dedup_counters = _make_dedup_counters(run_log)
    intake = ParserIntake(
        parser=_BackfillingMatchedParser(
            url="https://example.com/post-enrich-alias",
            company="Acme",
            location="Hamburg",
            title="Platform Engineer",
            body="Fresh raw description " + "x" * 120,
        ),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
        run_log=run_log,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(discovered_stub)

    assert isinstance(outcome, PoolAdmitted)
    assert outcome.pool_admission.listing_id == listing_id
    _assert_dedup_recorded(dedup_counters, "judge_pending")
    assert card_store.get(listing_id) is None


@pytest.mark.parametrize(
    ("body", "drop_reason"),
    [
        ("   \n\t  ", "content_empty_body"),
        ("x" * 99, "content_too_short"),
    ],
)
def test_post_enrich_judge_pending_content_drop_stops_before_pool_and_card_refresh(
    tmp_path: Path,
    body: str,
    drop_reason: str,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    discovered_stub = PositionStub(
        url="https://example.com/post-enrich-alias",
        title="Discovered title",
        source="test",
        posted_date=date(2026, 5, 29),
    )

    original = PositionStub(
        url="https://example.com/original-tuple",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
    )
    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    dedup_store.mark_matched(original)
    listing_id = dedup_store.listing_id_for(original.url)
    assert listing_id is not None
    card_store.put(
        listing_id,
        CardExtract(
            header="Persisted header",
            summary="Persisted summary",
            body="Persisted body",
        ),
    )
    run_log = RunLog(logs_dir)
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_BackfillingMatchedParser(
            url="https://example.com/post-enrich-alias",
            company="Acme",
            location="Hamburg",
            title="Platform Engineer",
            body=body,
        ),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(discovered_stub)

    assert isinstance(outcome, Dropped)
    assert not isinstance(outcome, PoolAdmitted)
    assert not isinstance(outcome, ClassifyForwarded)
    assert outcome.reason == drop_reason
    assert outcome.listing_id == listing_id
    assert outcome.dedup_kind == "judge_pending"
    _assert_dedup_recorded(dedup_counters, "judge_pending")
    assert _last_body(metrics_display, "parser test gates") == "1 content"
    assert card_store.get(listing_id) == CardExtract(
        header="Persisted header",
        summary="Persisted summary",
        body="Persisted body",
    )


@pytest.mark.parametrize(
    ("posted_date", "deadline", "reason", "age_days", "deadline_text"),
    [
        (date(2026, 4, 1), None, "too_old", 59, None),
        (None, date(2026, 5, 29), "deadline_passed", None, "2026-05-29"),
    ],
)
def test_post_enrich_judge_pending_backfilled_freshness_drop_stops_before_pool_and_content_and_expires_matched_record(
    tmp_path: Path,
    posted_date: date | None,
    deadline: date | None,
    reason: str,
    age_days: int | None,
    deadline_text: str | None,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    discovered_stub = PositionStub(
        url="https://example.com/post-enrich-alias",
        title="Discovered title",
        source="test",
    )

    original = PositionStub(
        url="https://example.com/original-tuple",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
    )
    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    dedup_store.mark_matched(original)
    listing_id = dedup_store.listing_id_for(original.url)
    assert listing_id is not None
    card_store.put(
        listing_id,
        CardExtract(
            header="Persisted header",
            summary="Persisted summary",
            body="Persisted body",
        ),
    )
    run_log = RunLog(logs_dir)
    display = FakeStatusDisplay()
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_BackfillingMatchedFreshnessDropParser(
            url="https://example.com/post-enrich-alias",
            company="Acme",
            location="Hamburg",
            title="Platform Engineer",
            posted_date=posted_date,
            deadline=deadline,
            body="Fresh raw description " + "x" * 120,
        ),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=display,
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=cast(Any, _UnexpectedContentGate()),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(discovered_stub)

    assert isinstance(outcome, Dropped)
    assert not isinstance(outcome, PoolAdmitted)
    assert not isinstance(outcome, ClassifyForwarded)
    assert outcome.reason == "freshness_post_enrich"
    assert outcome.stub == PositionStub(
        url="https://example.com/post-enrich-alias",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=posted_date,
        deadline=deadline,
    )
    assert outcome.listing_id == listing_id
    assert outcome.dedup_kind == "judge_pending"
    _assert_dedup_recorded(dedup_counters, "judge_pending")
    assert _last_body(metrics_display, "parser test gates") == "1 freshness"
    assert card_store.get(listing_id) is None

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[str(listing_id)]["status"] == "expired"
    assert seen_data[str(listing_id)]["urls"] == [
        "https://example.com/post-enrich-alias",
        "https://example.com/original-tuple",
    ]

    transcript_rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "freshness.transcripts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert transcript_rows == [
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


def test_parser_enrich_failed_returns_retryable_outcome_without_seen_or_card_write(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    stub = PositionStub(
        url="https://example.com/enrich-failed",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    run_log = RunLog(logs_dir)
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_EnrichFailedParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(stub)

    assert isinstance(outcome, RetryableEnrichFailure)
    assert outcome.stub == stub
    assert str(outcome.error) == "native enrich failed"
    _assert_dedup_recorded(dedup_counters, "miss")
    assert _last_body(metrics_display, "parser test") == (
        "0 discovered · 1 enrich_failed · 0 forwarded"
    )
    assert [
        {k: v for k, v in row.items() if k != "ts"}
        for row in _read_event_rows(logs_dir, "pipeline", "orchestrator")
    ] == [
        {
            "event": "enrich_failed",
            "url": stub.url,
            "source": stub.source,
        }
    ]
    assert card_store.get(1) is None
    assert not seen_path.exists()


def test_post_enrich_empty_body_returns_reasoned_content_drop_without_seen_or_card_write(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    stub = PositionStub(
        url="https://example.com/empty-body",
        title="Platform Engineer",
        source="test",
        posted_date=date(2026, 5, 29),
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    run_log = RunLog(logs_dir)
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_EmptyBodyParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(stub)

    assert isinstance(outcome, Dropped)
    assert not isinstance(outcome, PoolAdmitted)
    assert not isinstance(outcome, ClassifyForwarded)
    assert outcome.reason == "content_empty_body"
    assert outcome.stub == PositionStub(
        url="https://example.com/empty-body",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )
    assert outcome.listing_id == 1
    assert outcome.dedup_kind == "run_hit"
    _assert_dedup_recorded(dedup_counters, "run_hit")
    assert _last_body(metrics_display, "parser test gates") == "1 content"
    assert card_store.get(1) is None
    assert not seen_path.exists()

    transcript_rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "content.transcripts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert transcript_rows == [
        {
            "url": "https://example.com/empty-body",
            "title": "Platform Engineer",
            "source": "test",
            "passes": False,
            "reason": "empty_body",
            "body_len": 7,
        }
    ]


def test_post_enrich_too_short_body_returns_reasoned_content_drop_without_seen_or_card_write(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    stub = PositionStub(
        url="https://example.com/too-short-body",
        title="Platform Engineer",
        source="test",
        posted_date=date(2026, 5, 29),
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    run_log = RunLog(logs_dir)
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_TooShortBodyParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(stub)

    assert isinstance(outcome, Dropped)
    assert not isinstance(outcome, PoolAdmitted)
    assert not isinstance(outcome, ClassifyForwarded)
    assert outcome.reason == "content_too_short"
    assert outcome.stub == PositionStub(
        url="https://example.com/too-short-body",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )
    assert outcome.listing_id == 1
    assert outcome.dedup_kind == "run_hit"
    _assert_dedup_recorded(dedup_counters, "run_hit")
    assert _last_body(metrics_display, "parser test gates") == "1 content"
    assert card_store.get(1) is None
    assert not seen_path.exists()

    transcript_rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "content.transcripts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert transcript_rows == [
        {
            "url": "https://example.com/too-short-body",
            "title": "Platform Engineer",
            "source": "test",
            "passes": False,
            "reason": "too_short",
            "body_len": 99,
        }
    ]


def test_parser_oversized_body_returns_skip_outcome_without_seen_or_card_write(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    stub = PositionStub(
        url="https://example.com/oversized",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    run_log = RunLog(logs_dir)
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_OversizedBodyParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(stub)

    assert isinstance(outcome, OversizedBodySkip)
    assert outcome.stub == stub
    assert outcome.error.url == stub.url
    assert outcome.error.source == stub.source
    assert outcome.error.body_len == 4321
    _assert_dedup_recorded(dedup_counters, "miss")
    assert _last_body(metrics_display, "parser test") == "0 discovered · 0 forwarded"
    assert [
        {k: v for k, v in row.items() if k != "ts"}
        for row in _read_event_rows(logs_dir, "llm", "enricher")
    ] == [
        {
            "event": "body_oversized",
            "url": stub.url,
            "source": stub.source,
            "body_len": 4321,
        }
    ]
    assert not isinstance(outcome, PoolAdmitted)
    assert card_store.get(1) is None
    assert not seen_path.exists()


def test_parser_transient_http_error_returns_skip_outcome_without_seen_or_card_write(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    stub = PositionStub(
        url="https://example.com/transient",
        title="Platform Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=date(2026, 5, 29),
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    run_log = RunLog(logs_dir)
    dedup_counters = _make_dedup_counters(run_log)
    metrics, metrics_display = _make_run_metrics(run_log)
    intake = ParserIntake(
        parser_id="test",
        parser=_TransientHttpErrorParser(),
        freshness_gate=FreshnessGate(
            anchored_today=date(2026, 5, 30),
            max_listing_age_days=30,
            dedup=dedup_store,
            display=FakeStatusDisplay(),
            run_log=run_log,
        ),
        deduplication=dedup_store,
        dedup_counters=dedup_counters,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=FakeStatusDisplay(), run_log=run_log),
        card_store=card_store,
        run_log=run_log,
        metrics=metrics,
    )

    with dedup_store.run_scope():
        outcome = intake.process_position_stub(stub)

    assert isinstance(outcome, TransientHttpSkip)
    assert outcome.stub == stub
    assert "503 Service Unavailable" in str(outcome.error)
    _assert_dedup_recorded(dedup_counters, "miss")
    assert _last_body(metrics_display, "parser test") == "0 discovered · 0 forwarded"
    assert [
        {k: v for k, v in row.items() if k != "ts"}
        for row in _read_event_rows(logs_dir, "llm", "enricher")
    ] == [
        {
            "event": "fetch_transient_error",
            "url": stub.url,
            "source": stub.source,
            "error": "503 Service Unavailable",
        }
    ]
    assert not isinstance(outcome, PoolAdmitted)
    assert card_store.get(1) is None
    assert not seen_path.exists()
