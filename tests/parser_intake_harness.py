from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fake_status_display import FakeStatusDisplay

from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import DeduplicationStore, load as load_dedup
from application_pipeline.dedup_counters import DedupCounters, DedupSnapshot
from application_pipeline.extracts import CardStore, load_card_store
from application_pipeline.extracts.card_store import CardExtract
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.parser_intake import ParserIntake
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import Parser, PositionStub
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
    parser: Parser
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
    ) -> "ParserIntakeHarness":
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
        parser_instance = parser or InMemoryParser(
            EnrichResult(stub=enriched_stub, body=body, mode="native")
        )
        classify_sink = CollectingClassifySink()
        pool_collector = CollectingPoolCollector()
        parser_intake = ParserIntake(
            parser_id=parser_id,
            parser=parser_instance,
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
            parser=parser_instance,
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
            seen_path=seen_path,
            extracts_path=extracts_path,
            logs_dir=logs_dir,
            default_position_stub=discovered_stub,
            default_enriched_stub=enriched_stub,
            default_body=body,
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

    def seed_matched_pool_reentry_listing(
        self, stub: PositionStub | None = None
    ) -> int:
        listing = self.default_enriched_stub if stub is None else stub
        self.dedup_store.mark_matched(listing)
        listing_id = self.dedup_store.listing_id_for(listing.url)
        assert listing_id is not None
        return listing_id

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

    def classify_handoffs(self) -> list[ClassifyCall]:
        return list(self.classify_sink.calls)

    def pool_admissions(self) -> list[PoolAdmission]:
        return list(self.pool_collector.admissions)

    def card_content(self, listing_id: int) -> CardExtract | None:
        return self.card_store.get(listing_id)

    def dedup_status(self, listing_id: int) -> str | None:
        record = self.dedup_observation(listing_id)
        return None if record is None else record.status

    def dedup_observation(self, listing_id: int) -> DeduplicationObservation | None:
        record = self.dedup_store._records.get(listing_id)
        if record is None:
            return None
        return DeduplicationObservation(
            listing_id=listing_id,
            urls=tuple(str(url) for url in record.get("urls", [])),
            status=str(record.get("status")),
            company_lc=_optional_str(record.get("company_lc")),
            title_lc=_optional_str(record.get("title_lc")),
            location_lc=_optional_str(record.get("location_lc")),
            status_last_changed=_optional_str(record.get("status_last_changed")),
        )

    def dedup_counter_snapshot(self) -> DedupSnapshot:
        return self.dedup_counters.snapshot()

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
