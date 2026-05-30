from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fake_status_display import FakeStatusDisplay

from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import DeduplicationStore, load as load_dedup
from application_pipeline.dedup_counters import DedupCounters
from application_pipeline.extracts import CardStore, load_card_store
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.parser_intake import ParserIntake
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import PositionStub
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


@dataclass(frozen=True)
class ClassifyCall:
    listing_id: int
    stub: PositionStub
    body: str


@dataclass(frozen=True)
class PoolAdmission:
    listing_id: int
    stub: PositionStub


class InMemoryParser:
    def __init__(self, enrich_result: EnrichResult) -> None:
        self._enrich_result = enrich_result

    def __enter__(self) -> InMemoryParser:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        return self._enrich_result


class CollectingClassifySink:
    def __init__(self) -> None:
        self.calls: list[ClassifyCall] = []

    def enqueue(self, *, listing_id: int, stub: PositionStub, body: str) -> None:
        self.calls.append(ClassifyCall(listing_id=listing_id, stub=stub, body=body))


class CollectingPoolCollector:
    def __init__(self) -> None:
        self.admissions: list[PoolAdmission] = []

    def add_judge_pending(self, stub: PositionStub, listing_id: int) -> None:
        self.admissions.append(PoolAdmission(listing_id=listing_id, stub=stub))


@dataclass
class ParserIntakeHarness:
    parser_intake: ParserIntake
    parser: InMemoryParser
    dedup_store: DeduplicationStore
    dedup_counters: DedupCounters
    card_store: CardStore
    freshness_gate: FreshnessGate
    content_gate: ContentGate
    domain_pre_filter: PreFilterGate
    run_log: RunLog
    metrics: RunMetrics
    status_display: FakeStatusDisplay
    classify_sink: CollectingClassifySink
    pool_collector: CollectingPoolCollector
    default_position_stub: PositionStub
    default_enriched_stub: PositionStub
    default_body: str

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
    ) -> ParserIntakeHarness:
        seen_path = tmp_path / ".seen.json"
        extracts_path = tmp_path / "extracts.json"
        logs_dir = tmp_path / "logs"
        status_display = FakeStatusDisplay()
        run_log = RunLog(logs_dir)
        card_store = load_card_store(extracts_path)
        dedup_store = load_dedup(seen_path, card_store=card_store, run_log=run_log)
        dedup_counters = DedupCounters(display=status_display, run_log=run_log)
        freshness_gate = FreshnessGate(
            anchored_today=anchored_today,
            max_listing_age_days=max_listing_age_days,
            dedup=dedup_store,
            display=status_display,
            run_log=run_log,
            card_store=card_store,
        )
        content_gate = ContentGate(display=status_display, run_log=run_log)
        domain_pre_filter = PreFilterGate(
            blacklist=[],
            dedup=dedup_store,
            display=status_display,
            run_log=run_log,
        )
        metrics = RunMetrics(status_display, run_log=run_log)
        metrics.register_parser(
            parser_id,
            order=0,
            total_queries=1,
            has_native_enrich=True,
        )
        parser = InMemoryParser(
            EnrichResult(stub=enriched_stub, body=body, mode="native")
        )
        classify_sink = CollectingClassifySink()
        pool_collector = CollectingPoolCollector()
        parser_intake = ParserIntake(
            parser_id=parser_id,
            parser=parser,
            freshness_gate=freshness_gate,
            deduplication=dedup_store,
            dedup_counters=dedup_counters,
            domain_pre_filter=domain_pre_filter,
            content_gate=content_gate,
            card_store=card_store,
            pool_collector=pool_collector,
            classify_sink=classify_sink,
            run_log=run_log,
            metrics=metrics,
        )
        return cls(
            parser_intake=parser_intake,
            parser=parser,
            dedup_store=dedup_store,
            dedup_counters=dedup_counters,
            card_store=card_store,
            freshness_gate=freshness_gate,
            content_gate=content_gate,
            domain_pre_filter=domain_pre_filter,
            run_log=run_log,
            metrics=metrics,
            status_display=status_display,
            classify_sink=classify_sink,
            pool_collector=pool_collector,
            default_position_stub=discovered_stub,
            default_enriched_stub=enriched_stub,
            default_body=body,
        )

    def process_one_position_stub(self, stub: PositionStub | None = None) -> None:
        with self.dedup_store.run_scope():
            self.parser_intake.process_position_stub(
                self.default_position_stub if stub is None else stub
            )
