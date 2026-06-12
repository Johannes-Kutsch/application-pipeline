from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Literal, Protocol, cast

import httpx

from fake_status_display import FakeStatusDisplay

from application_pipeline.classify_stage import ClassifyStageHandoff
from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import (
    DeduplicationStore,
    load as load_dedup,
)
from application_pipeline.dedup_counters import DedupCounters, DedupSnapshot
from application_pipeline.extracts import CardStore, load_card_store
from application_pipeline.extracts.card_store import CardExtract
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.parser_intake import ParserIntake
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import Parser, PositionStub
from application_pipeline.parsers.body_fetch import OversizedBodyError
from application_pipeline.parsers.types import EnrichFailedError
from application_pipeline.parsers.types import EnrichResult
from application_pipeline.prefilter_gate import PreFilterGate
from application_pipeline.run_metrics import RunMetrics

DEFAULT_PARSER_ID = "parser_test"
DEFAULT_ANCHORED_TODAY = date(2026, 5, 30)
DEFAULT_DISCOVERED_STUB = PositionStub(
    url="https://example.com/fresh-forward",
    title="Backend Engineer",
    source="test",
    posted_date=date(2026, 5, 29),
)
DEFAULT_ENRICHED_STUB = PositionStub(
    url=DEFAULT_DISCOVERED_STUB.url,
    title=DEFAULT_DISCOVERED_STUB.title,
    source=DEFAULT_DISCOVERED_STUB.source,
    company="Acme",
    location="Hamburg",
    posted_date=DEFAULT_DISCOVERED_STUB.posted_date,
)
DEFAULT_BODY = "Fresh backend role " + "x" * 120
POST_ENRICH_ALIAS_URL = "https://example.com/post-enrich-alias"
POST_ENRICH_ORIGINAL_URL = "https://example.com/original-tuple"
HarnessSeedHelper = Callable[["ParserIntakeHarness"], int]


class ContentGateLike(Protocol):
    def inspect(self, body: str, stub: PositionStub) -> object: ...

    def snapshot(self) -> object: ...


class FreshnessGateLike(Protocol):
    def admit(
        self,
        stub: PositionStub,
        *,
        gate_arm: Literal["discover", "post_enrich", "post_llm"],
        deadline: date | None,
    ) -> bool: ...

    def snapshot(self) -> object: ...


class PreFilterGateLike(Protocol):
    def admit(self, stub: PositionStub) -> bool: ...

    def snapshot(self) -> object: ...


@dataclass(frozen=True)
class ClassifyCall:
    listing_id: int
    stub: PositionStub
    body: str
    parser_id: str


@dataclass(frozen=True)
class PoolAdmission:
    listing_id: int
    stub: PositionStub


@dataclass(frozen=True)
class DeduplicationObservation:
    listing_id: int
    urls: tuple[str, ...]
    status: str
    company_lc: str | None
    title_lc: str | None
    location_lc: str | None
    status_last_changed: str | None


class InMemoryParser:
    def __init__(
        self,
        enrich_result: EnrichResult,
        *,
        enrich_error: Exception | None = None,
    ) -> None:
        self._enrich_result = enrich_result
        self._enrich_error = enrich_error

    def __enter__(self) -> InMemoryParser:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        if self._enrich_error is not None:
            raise self._enrich_error
        return self._enrich_result

    def set_enrich_result(self, enrich_result: EnrichResult) -> None:
        self._enrich_result = enrich_result
        self._enrich_error = None

    def set_enrich_error(self, enrich_error: Exception | None) -> None:
        self._enrich_error = enrich_error


class CollectingClassifyHandoff:
    def __init__(self) -> None:
        self.calls: list[ClassifyCall] = []

    def submit_ready(
        self,
        *,
        listing_id: int,
        stub: PositionStub,
        raw_description: str,
        parser_id: str,
    ) -> None:
        self.calls.append(
            ClassifyCall(
                listing_id=listing_id,
                stub=stub,
                body=raw_description,
                parser_id=parser_id,
            )
        )


class CollectingPoolCollector:
    def __init__(self) -> None:
        self.admissions: list[PoolAdmission] = []

    def add_judge_pending(self, stub: PositionStub, listing_id: int) -> None:
        self.admissions.append(PoolAdmission(listing_id=listing_id, stub=stub))


class UnexpectedEnrichParser:
    def __enter__(self) -> "UnexpectedEnrichParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        raise AssertionError("discover-arm freshness drop must stop before enrich()")


class PassThroughPreFilter:
    def admit(self, stub: PositionStub) -> bool:
        return True

    def snapshot(self) -> object:
        return None


class CountingPreFilter:
    def __init__(self) -> None:
        self.calls = 0

    def admit(self, stub: PositionStub) -> bool:
        self.calls += 1
        return True

    def snapshot(self) -> object:
        return None


class FailOnPostEnrichFreshnessGate:
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

    def snapshot(self) -> object:
        return None


class UnexpectedContentGate:
    def inspect(self, body: str, stub: PositionStub) -> object:
        raise AssertionError("post-enrich dedup drop must stop before Content Gate")

    def snapshot(self) -> object:
        return None


@dataclass
class ParserIntakeHarness:
    parser_intake: ParserIntake
    parser: Parser
    dedup_store: DeduplicationStore
    dedup_counters: DedupCounters
    card_store: CardStore
    freshness_gate: FreshnessGateLike
    content_gate: ContentGateLike
    domain_pre_filter: PreFilterGateLike
    run_log: RunLog
    metrics: RunMetrics
    status_display: FakeStatusDisplay
    classify_handoff: CollectingClassifyHandoff
    pool_collector: CollectingPoolCollector
    seen_path: Path
    extracts_path: Path
    logs_dir: Path
    default_position_stub: PositionStub
    default_enriched_stub: PositionStub
    default_body: str
    _run_scope_depth: int = 0

    @classmethod
    def create(
        cls,
        tmp_path: Path,
        *,
        parser_id: str = DEFAULT_PARSER_ID,
        anchored_today: date = DEFAULT_ANCHORED_TODAY,
        max_listing_age_days: int = 30,
        discovered_stub: PositionStub = DEFAULT_DISCOVERED_STUB,
        enriched_stub: PositionStub = DEFAULT_ENRICHED_STUB,
        body: str = DEFAULT_BODY,
        parser: Parser | None = None,
        parser_enrich_error: Exception | None = None,
        freshness_gate: FreshnessGateLike | None = None,
        content_gate: ContentGateLike | None = None,
        domain_pre_filter: PreFilterGateLike | None = None,
        negative_keywords: list[str] | None = None,
    ) -> "ParserIntakeHarness":
        seen_path = tmp_path / ".seen.json"
        extracts_path = tmp_path / "extracts.json"
        logs_dir = tmp_path / "logs"
        status_display = FakeStatusDisplay()
        run_log = RunLog(logs_dir)
        card_store = load_card_store(extracts_path)
        dedup_store = load_dedup(seen_path, card_store=card_store, run_log=run_log)
        dedup_counters = DedupCounters(display=status_display, run_log=run_log)
        if freshness_gate is None:
            configured_freshness_gate: FreshnessGateLike = FreshnessGate(
                anchored_today=anchored_today,
                max_listing_age_days=max_listing_age_days,
                dedup=dedup_store,
                display=status_display,
                run_log=run_log,
                card_store=card_store,
            )
        else:
            configured_freshness_gate = freshness_gate
        if content_gate is None:
            configured_content_gate: ContentGateLike = ContentGate(run_log=run_log)
        else:
            configured_content_gate = content_gate
        if domain_pre_filter is None:
            configured_pre_filter: PreFilterGateLike = PreFilterGate(
                blacklist=[] if negative_keywords is None else negative_keywords,
                dedup=dedup_store,
                display=status_display,
                run_log=run_log,
            )
        else:
            configured_pre_filter = domain_pre_filter
        metrics = RunMetrics(status_display, run_log=run_log)
        metrics.register_parser(
            parser_id,
            order=0,
            total_queries=1,
            has_native_enrich=True,
        )
        parser_instance = parser or InMemoryParser(
            EnrichResult(stub=enriched_stub, body=body, mode="native"),
            enrich_error=parser_enrich_error,
        )
        classify_handoff = CollectingClassifyHandoff()
        pool_collector = CollectingPoolCollector()
        parser_intake = ParserIntake(
            parser_id=parser_id,
            parser=parser_instance,
            freshness_gate=cast(FreshnessGate, configured_freshness_gate),
            deduplication=dedup_store,
            dedup_counters=dedup_counters,
            domain_pre_filter=cast(PreFilterGate, configured_pre_filter),
            content_gate=cast(ContentGate, configured_content_gate),
            card_store=card_store,
            pool_collector=pool_collector,
            classify_handoff=cast(ClassifyStageHandoff, classify_handoff),
            run_log=run_log,
            metrics=metrics,
        )
        return cls(
            parser_intake=parser_intake,
            parser=parser_instance,
            dedup_store=dedup_store,
            dedup_counters=dedup_counters,
            card_store=card_store,
            freshness_gate=configured_freshness_gate,
            content_gate=configured_content_gate,
            domain_pre_filter=configured_pre_filter,
            run_log=run_log,
            metrics=metrics,
            status_display=status_display,
            classify_handoff=classify_handoff,
            pool_collector=pool_collector,
            seen_path=seen_path,
            extracts_path=extracts_path,
            logs_dir=logs_dir,
            default_position_stub=discovered_stub,
            default_enriched_stub=enriched_stub,
            default_body=body,
        )

    @classmethod
    def create_post_enrich_alias(
        cls,
        tmp_path: Path,
        *,
        parser_id: str = "test",
        content_gate: ContentGateLike | None = None,
        discovered_posted_date: date | None = date(2026, 5, 29),
    ) -> "ParserIntakeHarness":
        return cls.create(
            tmp_path,
            parser_id=parser_id,
            discovered_stub=cls.post_enrich_discovered_stub(
                posted_date=discovered_posted_date
            ),
            content_gate=content_gate,
        )

    @staticmethod
    def post_enrich_discovered_stub(
        *,
        posted_date: date | None = date(2026, 5, 29),
    ) -> PositionStub:
        return PositionStub(
            url=POST_ENRICH_ALIAS_URL,
            title="Discovered title",
            source="test",
            posted_date=posted_date,
        )

    @staticmethod
    def post_enrich_enriched_stub(
        *,
        posted_date: date | None = date(2026, 5, 29),
        deadline: date | None = None,
    ) -> PositionStub:
        return PositionStub(
            url=POST_ENRICH_ALIAS_URL,
            title="Platform Engineer",
            source="test",
            company="Acme",
            location="Hamburg",
            posted_date=posted_date,
            deadline=deadline,
        )

    @staticmethod
    def post_enrich_original_stub() -> PositionStub:
        return PositionStub(
            url=POST_ENRICH_ORIGINAL_URL,
            title="Platform Engineer",
            source="test",
            company="Acme",
            location="Hamburg",
        )

    @contextmanager
    def run_scope(self) -> Iterator[None]:
        if self._run_scope_depth > 0:
            yield
            return

        with self.dedup_store.run_scope():
            self._run_scope_depth += 1
            try:
                yield
            finally:
                self._run_scope_depth -= 1

    def process_one_position_stub(self, stub: PositionStub | None = None) -> None:
        with self.run_scope():
            self.parser_intake.process_position_stub(
                self.default_position_stub if stub is None else stub
            )

    def seed_out_of_domain_listing(self, stub: PositionStub | None = None) -> int:
        listing = self.default_enriched_stub if stub is None else stub
        self.dedup_store.mark_out_of_domain(listing)
        listing_id = self.dedup_store.listing_id_for(listing.url)
        assert listing_id is not None
        return listing_id

    def seed_post_discover_url_hit_listing(
        self, stub: PositionStub | None = None
    ) -> int:
        listing = self.default_position_stub if stub is None else stub
        self.dedup_store.mark_out_of_domain(listing)
        listing_id = self.dedup_store.listing_id_for(listing.url)
        assert listing_id is not None
        return listing_id

    def seed_post_discover_tuple_hit_listing(
        self, stub: PositionStub | None = None
    ) -> int:
        listing = self.default_position_stub if stub is None else stub
        original = PositionStub(
            url="https://example.com/original",
            title=listing.title,
            source=listing.source,
            company=listing.company,
            location=listing.location,
        )
        self.dedup_store.mark_out_of_domain(original)
        listing_id = self.dedup_store.listing_id_for(original.url)
        assert listing_id is not None
        return listing_id

    def seed_post_discover_fuzzy_hit_listing(
        self, stub: PositionStub | None = None
    ) -> int:
        listing = self.default_position_stub if stub is None else stub
        original = PositionStub(
            url="https://example.com/original",
            title="Senior Lead Platform Backend Engineer",
            source=listing.source,
            company=listing.company,
            location=listing.location,
        )
        self.dedup_store.mark_out_of_domain(original)
        listing_id = self.dedup_store.listing_id_for(original.url)
        assert listing_id is not None
        return listing_id

    def seed_post_discover_run_hit_listing(
        self, stub: PositionStub | None = None
    ) -> int:
        listing = self.default_position_stub if stub is None else stub
        assert self._run_scope_depth > 0, (
            "run_scope() must stay open for in-run post-discover seeding"
        )
        return self.dedup_store.is_seen(listing).listing_id

    def seed_post_enrich_url_hit_listing(self, stub: PositionStub | None = None) -> int:
        listing = self.default_enriched_stub if stub is None else stub
        self.dedup_store.mark_out_of_domain(listing)
        listing_id = self.dedup_store.listing_id_for(listing.url)
        assert listing_id is not None
        return listing_id

    def seed_post_enrich_tuple_hit_listing(
        self, stub: PositionStub | None = None
    ) -> int:
        listing = self.default_enriched_stub if stub is None else stub
        original = PositionStub(
            url="https://example.com/original-tuple",
            title=listing.title,
            source=listing.source,
            company=listing.company,
            location=listing.location,
        )
        self.dedup_store.mark_out_of_domain(original)
        listing_id = self.dedup_store.listing_id_for(original.url)
        assert listing_id is not None
        return listing_id

    def seed_post_enrich_fuzzy_hit_listing(
        self, stub: PositionStub | None = None
    ) -> int:
        listing = self.default_enriched_stub if stub is None else stub
        original = PositionStub(
            url="https://example.com/original-fuzzy",
            title="Senior Lead Platform Backend Engineer",
            source=listing.source,
            company=listing.company,
            location=listing.location,
        )
        self.dedup_store.mark_out_of_domain(original)
        listing_id = self.dedup_store.listing_id_for(original.url)
        assert listing_id is not None
        return listing_id

    def seed_matched_pool_reentry_listing(
        self, stub: PositionStub | None = None
    ) -> int:
        return self.seed_judge_pending_listing(stub)

    def seed_in_run_pending_listing(self, stub: PositionStub | None = None) -> int:
        listing = self.default_position_stub if stub is None else stub
        assert self._run_scope_depth > 0, (
            "run_scope() must stay open for in-run seeding"
        )
        return self.dedup_store.is_seen(listing).listing_id

    def seed_persisted_card(
        self,
        listing_id: int,
        *,
        header: str = "Persisted header",
        summary: str = "Persisted summary",
        body: str = "Persisted body",
    ) -> CardExtract:
        extract = CardExtract(header=header, summary=summary, body=body)
        self.card_store.put(listing_id, extract)
        return extract

    def seed_judge_pending_listing(
        self,
        stub: PositionStub | None = None,
        *,
        card: CardExtract | None = None,
    ) -> int:
        listing = self.default_enriched_stub if stub is None else stub
        self.dedup_store.mark_matched(listing)
        listing_id = self.dedup_store.listing_id_for(listing.url)
        assert listing_id is not None
        if card is not None:
            self.card_store.put(listing_id, card)
        return listing_id

    def seed_judge_pending_listing_with_persisted_card(
        self,
        stub: PositionStub | None = None,
        *,
        header: str = "Persisted header",
        summary: str = "Persisted summary",
        body: str = "Persisted body",
    ) -> int:
        listing_id = self.seed_judge_pending_listing(stub)
        self.seed_persisted_card(
            listing_id,
            header=header,
            summary=summary,
            body=body,
        )
        return listing_id

    def seed_post_enrich_judge_pending_listing(
        self,
        *,
        card: CardExtract | None = None,
    ) -> int:
        return self.seed_judge_pending_listing(
            self.post_enrich_original_stub(),
            card=card,
        )

    def set_parser_enrich_result(
        self,
        *,
        stub: PositionStub | None = None,
        body: str | None = None,
        mode: Literal["native", "fallback"] = "native",
    ) -> None:
        assert isinstance(self.parser, InMemoryParser), (
            "set_parser_enrich_result() requires the default InMemoryParser"
        )
        self.parser.set_enrich_result(
            EnrichResult(
                stub=self.default_enriched_stub if stub is None else stub,
                body=self.default_body if body is None else body,
                mode=mode,
            )
        )

    def set_parser_enrich_failed_error(
        self,
        message: str = "native enrich failed",
    ) -> None:
        self._set_parser_enrich_error(EnrichFailedError(message))

    def set_parser_oversized_body_error(
        self,
        *,
        stub: PositionStub | None = None,
        url: str | None = None,
        source: str | None = None,
        body_len: int = 4321,
    ) -> None:
        position_stub = self.default_position_stub if stub is None else stub
        self._set_parser_enrich_error(
            OversizedBodyError(
                url=position_stub.url if url is None else url,
                source=position_stub.source if source is None else source,
                body_len=body_len,
            )
        )

    def set_parser_transient_http_error(
        self,
        message: str = "503 Service Unavailable",
        *,
        stub: PositionStub | None = None,
        url: str | None = None,
        status_code: int = 503,
    ) -> None:
        position_stub = self.default_position_stub if stub is None else stub
        request_url = position_stub.url if url is None else url
        self._set_parser_enrich_error(
            httpx.HTTPStatusError(
                message,
                request=httpx.Request("GET", request_url),
                response=httpx.Response(status_code),
            )
        )

    def _set_parser_enrich_error(self, enrich_error: Exception | None) -> None:
        assert isinstance(self.parser, InMemoryParser), (
            "_set_parser_enrich_error() requires the default InMemoryParser"
        )
        self.parser.set_enrich_error(enrich_error)

    def set_parser_backfilled_enrich_result(
        self,
        *,
        title: str,
        company: str,
        location: str,
        posted_date: date | None = None,
        deadline: date | None = None,
        body: str | None = None,
        mode: Literal["native", "fallback"] = "native",
    ) -> None:
        self.set_parser_enrich_result(
            stub=PositionStub(
                url=self.default_position_stub.url,
                title=title,
                source=self.default_position_stub.source,
                company=company,
                location=location,
                posted_date=posted_date,
                deadline=deadline,
            ),
            body=body,
            mode=mode,
        )

    def set_post_enrich_alias_result(
        self,
        *,
        body: str,
        posted_date: date | None = date(2026, 5, 29),
        deadline: date | None = None,
        mode: Literal["native", "fallback"] = "native",
    ) -> PositionStub:
        stub = self.post_enrich_enriched_stub(
            posted_date=posted_date,
            deadline=deadline,
        )
        self.set_parser_enrich_result(stub=stub, body=body, mode=mode)
        return stub

    def classify_handoffs(self) -> list[ClassifyCall]:
        return list(self.classify_handoff.calls)

    def pool_admissions(self) -> list[PoolAdmission]:
        return list(self.pool_collector.admissions)

    def card_content(self, listing_id: int) -> CardExtract | None:
        return self.card_store.get(listing_id)

    def dedup_status(self, listing_id: int) -> str | None:
        record = self.dedup_observation(listing_id)
        return None if record is None else record.status

    def listing_id_for_url(self, url: str) -> int | None:
        return self.dedup_store.listing_id_for(url)

    def dedup_observation(self, listing_id: int) -> DeduplicationObservation | None:
        return _dedup_observation_from_records(self.dedup_store._records, listing_id)

    def persisted_dedup_observation(
        self, listing_id: int
    ) -> DeduplicationObservation | None:
        if not self.seen_path.exists():
            return None
        persisted_store = load_dedup(self.seen_path)
        return _dedup_observation_from_records(persisted_store._records, listing_id)

    def persisted_dedup_status(self, listing_id: int) -> str | None:
        record = self.persisted_dedup_observation(listing_id)
        return None if record is None else record.status

    def persisted_listing_id_for_url(self, url: str) -> int | None:
        if not self.seen_path.exists():
            return None
        persisted_store = load_dedup(self.seen_path)
        return persisted_store.listing_id_for(url)

    def persisted_card_content(self, listing_id: int) -> CardExtract | None:
        if not self.extracts_path.exists():
            return None
        return load_card_store(self.extracts_path).get(listing_id)

    def in_memory_listing_id_for_url(self, url: str) -> int | None:
        return self.dedup_store.listing_id_for(url)

    def content_snapshot(self) -> object:
        return self.content_gate.snapshot()

    def freshness_snapshot(self) -> object:
        return self.freshness_gate.snapshot()

    def prefilter_snapshot(self) -> object:
        return self.domain_pre_filter.snapshot()

    def dedup_counter_snapshot(self) -> DedupSnapshot:
        return self.dedup_counters.snapshot()

    def assert_dedup_recorded(self, expected: str | None) -> None:
        snapshot = self.dedup_counters.snapshot()
        assert snapshot.dedup_url_hits == (1 if expected == "url_hit" else 0)
        assert snapshot.dedup_tuple_hits == (1 if expected == "tuple_hit" else 0)
        assert snapshot.dedup_fuzzy_hits == (1 if expected == "fuzzy_hit" else 0)
        assert snapshot.dedup_run_hits == (1 if expected == "run_hit" else 0)
        assert snapshot.dedup_misses == (1 if expected == "miss" else 0)
        assert snapshot.judge_resumed == (1 if expected == "judge_pending" else 0)

    def log_artifact_event_rows(self, component_id: str) -> list[dict[str, object]]:
        return _read_jsonl_rows(
            self.logs_dir / _log_artifact_relpath(component_id, "events")
        )

    def log_artifact_transcript_rows(
        self, component_id: str
    ) -> list[dict[str, object]]:
        return _read_jsonl_rows(
            self.logs_dir / _log_artifact_relpath(component_id, "transcripts")
        )

    def status_display_row_bodies(self, row_name: str) -> list[str]:
        return self.status_display.body_updates_for(row_name)

    def status_display_row_body(self, row_name: str) -> str:
        bodies = self.status_display_row_bodies(row_name)
        assert bodies, f"no body updates for row {row_name!r}"
        return bodies[-1]


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _dedup_observation_from_records(
    records: dict[int, dict[str, object]], listing_id: int
) -> DeduplicationObservation | None:
    record = records.get(listing_id)
    if record is None:
        return None
    urls = record.get("urls")
    return DeduplicationObservation(
        listing_id=listing_id,
        urls=tuple(str(url) for url in urls) if isinstance(urls, list) else (),
        status=str(record.get("status")),
        company_lc=_optional_str(record.get("company_lc")),
        title_lc=_optional_str(record.get("title_lc")),
        location_lc=_optional_str(record.get("location_lc")),
        status_last_changed=_optional_str(record.get("status_last_changed")),
    )


def _log_artifact_relpath(component_id: str, artifact_kind: str) -> Path:
    parts = component_id.split("_", 1)
    if len(parts) == 2 and parts[0] in {"parser", "llm", "pipeline"}:
        return Path(parts[0]) / f"{parts[1]}.{artifact_kind}.jsonl"
    return Path(f"{component_id}.{artifact_kind}.jsonl")


def _read_jsonl_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
