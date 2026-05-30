from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
import traceback
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from application_pipeline import config as config_module
from application_pipeline import dedup as dedup_module
from application_pipeline.llm import quota as _quota
from application_pipeline.parser_log import RunLog
from application_pipeline._context import current_stage
from application_pipeline.dedup_counters import DedupCounters
from application_pipeline.run_metrics import RunMetrics, RunSummary
from application_pipeline.status_display import PlainStatusDisplay, StatusDisplay
from application_pipeline.config import ConfigError, SourceEntry
from application_pipeline.dedup import (
    DedupStoreError,
    DeduplicationStore,
)
from application_pipeline.extracts.card_store import CardStore, load_card_store
from application_pipeline.failure_report import write_failure as _write_failure
from application_pipeline.llm import (
    ClaudeExtractor,
    ExtractorError,
    JudgeCandidate,
    MatchVerdict,
)
from application_pipeline.llm.claude_cli import ClaudeUsageLimitError
from application_pipeline.llm.types import AppliedClassifyOutcome, CallUsage
from application_pipeline.llm_enricher import LLMEnricher, LLMExtractor
from application_pipeline.parsers import (
    NotServedQuery,
    Parser,
    ParserQuery,
    PositionStub,
)
from application_pipeline.parsers.types import City, Location, Remote
from application_pipeline.parsers import registry as _default_registry
from application_pipeline.parser_intake import (
    ClassifyForwarded,
    Dropped,
    OversizedBodySkip,
    ParserRowMetric,
    ParserIntake,
    PoolAdmitted,
    RetryableEnrichFailure,
    TransientHttpSkip,
)
from application_pipeline.content_gate import ContentGate
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.prefilter_gate import PreFilterGate
from application_pipeline.prompts import PromptError, load_prompts
from application_pipeline.daily_results_file import DailyResultsFile, ResultsFileError
from application_pipeline.search_terms import SearchTerms, load_search_terms

_log = logging.getLogger(__name__)

_STALL_THRESHOLD_S: float = 60.0


def _has_native_enrich(cls: type) -> bool:
    # Class attribute wins (test doubles); fall back to module-level declaration.
    v = cls.__dict__.get("has_native_enrich")
    if v is not None:
        return bool(v)
    module = sys.modules.get(cls.__module__)
    return bool(getattr(module, "has_native_enrich", False))


_ZERO_USAGE = CallUsage(
    input_tokens=0,
    output_tokens=0,
    cache_read_tokens=0,
    cost_usd=0.0,
    duration_s=0.0,
)


@runtime_checkable
class _LLMJudge(Protocol):
    def judge_top_n(
        self, candidates: list[JudgeCandidate]
    ) -> tuple[list[MatchVerdict], CallUsage]: ...


class _LLMEnricherLike(Protocol):
    def enrich(
        self, items: "list[tuple[int, PositionStub, str]]"
    ) -> AppliedClassifyOutcome: ...


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


class _RunState:
    """Consolidated run-state object replacing ad-hoc abort/degraded flags."""

    def __init__(self) -> None:
        self.degraded_reason: str | None = None
        self.fatal_exc: BaseException | None = None
        self.degraded_at: datetime | None = None
        self.degraded_by: str | None = None
        self._lock = threading.Lock()

    @property
    def is_degraded(self) -> bool:
        return self.degraded_reason is not None

    @property
    def is_aborted(self) -> bool:
        return self.fatal_exc is not None

    def set_degraded(self, reason: str, by: str) -> None:
        with self._lock:
            if self.degraded_reason is None:
                self.degraded_reason = reason
                self.degraded_at = datetime.now(timezone.utc)
                self.degraded_by = by

    def set_aborted(self, exc: BaseException) -> None:
        with self._lock:
            if self.fatal_exc is None:
                self.fatal_exc = exc


# ---------------------------------------------------------------------------
# Queue protocol sentinels
# ---------------------------------------------------------------------------


class _ParserDone:
    __slots__ = ()


@dataclass
class _ParserDead:
    exc: BaseException
    traceback_str: str


_PARSER_DONE = _ParserDone()


class _QueryDone:
    __slots__ = ()


_QUERY_DONE = _QueryDone()


# ---------------------------------------------------------------------------
# Classify queue protocol
# ---------------------------------------------------------------------------


@dataclass
class _ClassifyRequest:
    stub: PositionStub
    body: str
    parser_id: str
    listing_id: int = 0


class _NoMoreBatches:
    __slots__ = ()


_NO_MORE_BATCHES = _NoMoreBatches()


# ---------------------------------------------------------------------------
# Pool collector — thread-safe accumulator for judge candidates
# ---------------------------------------------------------------------------


class _PoolCollector:
    """Collects PositionStubs from enrich-complete and judge-pending paths."""

    def __init__(self) -> None:
        self._stubs: dict[int, PositionStub] = {}
        self._lock = threading.Lock()

    def add_matched(self, stub: PositionStub, listing_id: int) -> None:
        with self._lock:
            self._stubs[listing_id] = stub

    def add_judge_pending(self, stub: PositionStub, listing_id: int) -> None:
        with self._lock:
            self._stubs[listing_id] = stub

    def get_stub(self, listing_id: int) -> PositionStub | None:
        with self._lock:
            return self._stubs.get(listing_id)

    def build_candidates(self, card_store: CardStore) -> list[JudgeCandidate]:
        with self._lock:
            stubs = dict(self._stubs)
        candidates = []
        for listing_id in stubs:
            card = card_store.get(listing_id)
            if card is None:
                continue
            candidates.append(
                JudgeCandidate(id=listing_id, header=card.header, summary=card.summary)
            )
        return candidates

    @property
    def pool_size(self) -> int:
        with self._lock:
            return len(self._stubs)


# ---------------------------------------------------------------------------
# Queue worker base class
# ---------------------------------------------------------------------------


class _QueueWorker(threading.Thread):
    def __init__(
        self,
        *,
        input_queue: queue.Queue[object],
        sentinel: object,
        stage_name: str,
        run_state: "_RunState",
    ) -> None:
        super().__init__(daemon=True)
        self._input_queue = input_queue
        self._sentinel = sentinel
        self._stage_name = stage_name
        self._run_state = run_state
        self.exc: BaseException | None = None

    def run(self) -> None:
        current_stage.set(self._stage_name)
        try:
            while True:
                item = self._input_queue.get()
                if item is self._sentinel:
                    break
                self._on_dequeue(item)
                if not self._run_state.is_degraded:
                    self._process(item)
        except BaseException as exc:
            self.exc = exc
            self._run_state.set_aborted(exc)
        finally:
            self._on_shutdown()

    def _on_dequeue(self, item: object) -> None:
        pass

    def _on_shutdown(self) -> None:
        pass

    def _process(self, item: object) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Parser thread — runs full pre-LLM pipeline per stub inline
# ---------------------------------------------------------------------------


class _ParserThread(threading.Thread):
    def __init__(
        self,
        parser_id: str,
        parser: Parser,
        worklist: list[ParserQuery],
        outbound: queue.Queue[tuple[str, object]],
        classify_queue: queue.Queue[object],
        *,
        run_log: RunLog,
        run_state: _RunState,
        freshness: FreshnessGate,
        prefilter: PreFilterGate,
        content_gate: ContentGate,
        dedup: DeduplicationStore,
        dedup_counters: DedupCounters,
        pool_collector: _PoolCollector,
        metrics: RunMetrics,
        card_store: CardStore,
    ) -> None:
        super().__init__(name=f"parser-{parser_id}", daemon=True)
        self._parser_id = parser_id
        self._parser = parser
        self._worklist = worklist
        self._outbound = outbound
        self._classify_queue = classify_queue
        self._run_log = run_log
        self._run_state = run_state
        self._freshness = freshness
        self._prefilter = prefilter
        self._content_gate = content_gate
        self._dedup = dedup
        self._pool_collector = pool_collector
        self._metrics = metrics
        self._parser_intake = ParserIntake(
            parser_id=parser_id,
            parser=parser,
            freshness_gate=freshness,
            deduplication=dedup,
            dedup_counters=dedup_counters,
            domain_pre_filter=prefilter,
            content_gate=content_gate,
            card_store=card_store,
            run_log=run_log,
        )

    def run(self) -> None:
        try:
            for query in self._worklist:
                location_str = (
                    query.location.name
                    if isinstance(query.location, City)
                    else "Remote"
                )
                self._run_log.event(
                    "parser_" + self._parser_id,
                    "query_started",
                    keyword=query.keyword,
                    location=location_str,
                )
                try:
                    gen = iter(self._parser.discover(query))
                    try:
                        for item in gen:
                            if isinstance(item, NotServedQuery):
                                self._outbound.put((self._parser_id, item))
                                continue
                            if self._run_state.is_aborted:
                                break
                            self._process_stub(item)
                    finally:
                        close = getattr(gen, "close", None)
                        if close is not None:
                            close()
                    self._outbound.put((self._parser_id, _QUERY_DONE))
                finally:
                    self._run_log.event(
                        "parser_" + self._parser_id,
                        "query_ended",
                        keyword=query.keyword,
                        location=location_str,
                    )
        except BaseException as exc:
            self._outbound.put(
                (self._parser_id, _ParserDead(exc, traceback.format_exc()))
            )
        else:
            self._outbound.put((self._parser_id, _PARSER_DONE))

    def _process_stub(self, stub: PositionStub) -> None:
        self._metrics.discovered(self._parser_id)

        outcome = self._parser_intake.process_position_stub(stub)
        self._apply_parser_row_metric(outcome.parser_row_metric)

        if isinstance(outcome, RetryableEnrichFailure):
            return
        if isinstance(outcome, OversizedBodySkip):
            return
        if isinstance(outcome, TransientHttpSkip):
            return
        if isinstance(outcome, PoolAdmitted):
            self._pool_collector.add_judge_pending(
                outcome.pool_admission.stub,
                outcome.pool_admission.listing_id,
            )
            return
        if isinstance(outcome, Dropped):
            return

        assert isinstance(outcome, ClassifyForwarded)
        self._metrics.enriched(self._parser_id, outcome.enrich_mode)
        self._classify_queue.put(self._classify_request_from_outcome(outcome))
        self._metrics.classify_buffered(1)

    def _apply_parser_row_metric(self, metric: ParserRowMetric | None) -> None:
        if metric is None:
            return
        if metric == "enrich_failed":
            self._metrics.enrich_failed(self._parser_id)
            self._metrics.increment_enrich_failed_count(self._parser_id)
            return
        if metric == "freshness_dropped":
            self._metrics.increment_freshness_dropped(self._parser_id)
            return
        if metric == "dedup_dropped":
            self._metrics.increment_dedup_dropped(self._parser_id)
            return
        if metric == "prefilter_dropped":
            self._metrics.increment_prefilter_dropped(self._parser_id)
            return
        if metric == "content_dropped":
            self._metrics.increment_content_dropped(self._parser_id)
            return
        if metric == "forwarded":
            self._metrics.increment_forwarded(self._parser_id)
            return
        raise ValueError(f"unsupported parser-row metric: {metric}")

    def _classify_request_from_outcome(
        self, outcome: ClassifyForwarded
    ) -> _ClassifyRequest:
        return _ClassifyRequest(
            stub=outcome.stub,
            body=outcome.body,
            parser_id=outcome.parser_id,
            listing_id=outcome.listing_id,
        )


# ---------------------------------------------------------------------------
# Quota sleep helper
# ---------------------------------------------------------------------------


def _quota_sleep(err: ClaudeUsageLimitError, run_log: RunLog) -> None:
    now = datetime.now(timezone.utc)
    wake = _quota.compute_wake_time(err.reset_time, now)
    duration_s = max(0.0, (wake - now).total_seconds())
    run_log.event(
        "pipeline_orchestrator",
        "quota_sleep",
        reset_time=err.reset_time.isoformat() if err.reset_time is not None else None,
        wake_time=wake.isoformat(),
        duration_s=duration_s,
    )
    time.sleep(duration_s)


# ---------------------------------------------------------------------------
# Classify accumulator — fills batches sequentially from classify queue
# ---------------------------------------------------------------------------


class _ClassifyAccumulator(threading.Thread):
    """Single thread that fills classify batches sequentially.

    Pulls _ClassifyRequest items from classify_queue, assembles batches of
    batch_size, and puts each complete batch onto dispatch_queue. On sentinel,
    flushes any partial batch and then sends num_workers sentinels to workers.
    """

    def __init__(
        self,
        *,
        classify_queue: queue.Queue[object],
        dispatch_queue: "queue.Queue[object]",
        batch_size: int,
        num_workers: int,
        metrics: "RunMetrics",
        run_state: _RunState,
    ) -> None:
        super().__init__(name="classify-accumulator", daemon=True)
        self._classify_queue = classify_queue
        self._dispatch_queue = dispatch_queue
        self._batch_size = batch_size
        self._num_workers = num_workers
        self._metrics = metrics
        self._run_state = run_state
        self.exc: BaseException | None = None

    def run(self) -> None:
        current_stage.set("classify-accumulator")
        try:
            batch: list[_ClassifyRequest] = []
            while True:
                item = self._classify_queue.get()
                if item is _NO_MORE_BATCHES:
                    if batch:
                        self._metrics.classify_batch_dequeued(len(batch))
                        self._dispatch_queue.put(batch)
                    for _ in range(self._num_workers):
                        self._dispatch_queue.put(_NO_MORE_BATCHES)
                    break
                assert isinstance(item, _ClassifyRequest)
                batch.append(item)
                if len(batch) >= self._batch_size:
                    self._metrics.classify_batch_dequeued(len(batch))
                    self._dispatch_queue.put(batch)
                    batch = []
        except BaseException as exc:
            self.exc = exc
            self._run_state.set_aborted(exc)
            for _ in range(self._num_workers):
                self._dispatch_queue.put(_NO_MORE_BATCHES)


# ---------------------------------------------------------------------------
# Classify worker — runs only the LLM call + verdict handling
# ---------------------------------------------------------------------------


class _ClassifyWorker(_QueueWorker):
    def __init__(
        self,
        *,
        dispatch_queue: "queue.Queue[object]",
        pool_collector: "_PoolCollector",
        llm_enricher: "_LLMEnricherLike",
        dedup_store: DeduplicationStore,
        metrics: "RunMetrics",
        run_state: _RunState,
        run_log: RunLog,
        quota_wall: "_quota.QuotaWall",
        worker_index: int = 0,
    ) -> None:
        super().__init__(
            input_queue=dispatch_queue,
            sentinel=_NO_MORE_BATCHES,
            stage_name="classify",
            run_state=run_state,
        )
        self.name = f"classify-worker-{worker_index}"
        self._pool_collector = pool_collector
        self._llm_enricher = llm_enricher
        self._dedup_store = dedup_store
        self._metrics = metrics
        self._run_log = run_log
        self._quota_wall = quota_wall

    def run(self) -> None:
        current_stage.set(self._stage_name)
        try:
            while True:
                item = self._input_queue.get()
                if item is self._sentinel:
                    break
                assert isinstance(item, list)
                batch: list[_ClassifyRequest] = item
                if not self._run_state.is_degraded:
                    self._process_batch(batch)
        except BaseException as exc:
            self.exc = exc
            self._run_state.set_aborted(exc)
        finally:
            self._on_shutdown()

    def _process_batch(self, batch: list[_ClassifyRequest]) -> None:
        items = [(req.listing_id, req.stub, req.body) for req in batch]

        while True:
            self._quota_wall.wait_if_blocked()
            try:
                outcome = self._llm_enricher.enrich(items)
                break
            except ClaudeUsageLimitError as err:
                now = datetime.now(timezone.utc)
                wake = _quota.compute_wake_time(err.reset_time, now)
                duration_s = max(0.0, (wake - now).total_seconds())
                is_first = self._quota_wall.raise_wall(wake - _quota._BUFFER)
                if is_first:
                    self._run_log.event(
                        "pipeline_orchestrator",
                        "quota_sleep",
                        reset_time=(
                            err.reset_time.isoformat()
                            if err.reset_time is not None
                            else None
                        ),
                        wake_time=wake.isoformat(),
                        duration_s=duration_s,
                    )
            except ExtractorError as exc:
                _log.warning("llm_enricher.enrich failed: %s", exc)
                self._metrics.classify_batch_failed(len(batch))
                self._run_log.event(
                    "llm_classify_relevance",
                    "classify_relevance",
                    status="error",
                    error=str(exc),
                )
                return

        dropped = 0
        retryable = 0
        for req, item_outcome in zip(batch, outcome.items):
            self._run_log.event(
                "llm_classify_relevance",
                "classify_relevance",
                matches=item_outcome.event_matches,
            )
            if item_outcome.state == "retryable":
                retryable += 1
                self._metrics.enrich_failed(req.stub.source)
            elif item_outcome.state == "expired":
                dropped += 1
            elif item_outcome.state == "rejected":
                dropped += 1
            elif item_outcome.state == "matched":
                matched = item_outcome.matched_listing
                if matched is None or matched.listing_id != req.listing_id:
                    raise AssertionError("matched outcome missing matched listing data")

        for listing_id, stub in outcome.matched_listings:
            self._pool_collector.add_matched(stub, listing_id)

        self._metrics.classify_batch_complete(
            _ZERO_USAGE,
            len(batch),
            dropped,
            retryable_items=retryable,
        )


@dataclass
class _ParserState:
    parser_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_monotonic: float = field(default_factory=time.monotonic)
    last_event_monotonic: float = field(default_factory=time.monotonic)
    stall_logged: bool = False


# ---------------------------------------------------------------------------
# Outbound dispatcher — routes control signals only
# ---------------------------------------------------------------------------


class _OutboundDispatcher:
    """Routes control signals from the outbound queue to the appropriate handlers."""

    def __init__(
        self,
        *,
        parser_states: dict[str, "_ParserState"],
        metrics: "RunMetrics",
        run_log: RunLog,
        failures_dir: Path,
    ) -> None:
        self._parser_states = parser_states
        self._metrics = metrics
        self._run_log = run_log
        self._failures_dir = failures_dir

    def dispatch(self, pid: str, payload: object) -> bool:
        """Dispatch a control payload; returns True if the parser has finished."""
        if isinstance(payload, NotServedQuery):
            self._handle_not_served(pid)
        elif payload is _QUERY_DONE:
            self._handle_query_done(pid)
        elif payload is _PARSER_DONE:
            self._handle_parser_done(pid)
            return True
        elif isinstance(payload, _ParserDead):
            self._handle_parser_dead(pid, payload)
            return True
        return False

    def _handle_not_served(self, pid: str) -> None:
        self._metrics.not_served_query(pid)

    def _handle_query_done(self, pid: str) -> None:
        self._metrics.query_done(pid)

    def _handle_parser_done(self, pid: str) -> None:
        self._metrics.parser_done(pid)

    def _handle_parser_dead(self, pid: str, payload: _ParserDead) -> None:
        self._run_log.traceback("parser_" + pid, payload.traceback_str)
        self._metrics.parser_dead(pid)
        _write_failure(
            stage=f"parser:{pid}",
            error=payload.exc,
            log_tail=payload.traceback_str,
            failures_dir=self._failures_dir,
        )
        _write_failure(
            stage=f"parser:{pid}",
            error=payload.exc,
            log_tail=payload.traceback_str,
            failures_dir=self._failures_dir,
        )


# ---------------------------------------------------------------------------
# Extracts wipe helper (ADR-0024)
# ---------------------------------------------------------------------------


def _wipe_extracts_if_v1(path: Path) -> None:
    """Delete extracts.json if it contains v1-format records (pre-upgrade data)."""
    if not path.exists():
        return
    try:
        data = json.loads(path.read_bytes())
    except (json.JSONDecodeError, OSError):
        path.unlink(missing_ok=True)
        return
    if not isinstance(data, dict):
        path.unlink(missing_ok=True)
        return
    for record in data.values():
        if not isinstance(record, dict):
            path.unlink(missing_ok=True)
            return
        if "header" not in record or "summary" not in record:
            path.unlink(missing_ok=True)
            return


def run(
    config_path: Path,
    *,
    search_terms: SearchTerms | None = None,
    llm_enricher: "_LLMEnricherLike | None" = None,
    extractor: object = None,
    card_store: CardStore | None = None,
    parser_registry: Callable[[str], type[Parser] | None] | None = None,
    dedup_store: DeduplicationStore | None = None,
    status_display: StatusDisplay | None = None,
    run_log: RunLog | None = None,
    stall_threshold_s: float = _STALL_THRESHOLD_S,
    quota_wall: "_quota.QuotaWall | None" = None,
    no_judge: bool = False,
) -> RunSummary:
    anchored_today: date = datetime.now(timezone.utc).date()
    cron_anchored_date = anchored_today.isoformat()

    if status_display is None:
        status_display = PlainStatusDisplay(run_log=run_log)

    run_state = _RunState()
    _start = time.monotonic()
    status_display.register("pipeline", order=0, phase="running")
    status_display.register("startup", order=1, phase="running")
    try:
        # Step 1: Load config
        try:
            cfg = config_module.load(config_path)
        except ConfigError as exc:
            _log.error("startup failed — config: %s", exc)
            raise

        if run_log is None:
            run_log = RunLog(cfg.logs_path)

        # Steps 2-3: Load prompts, build extractor + LLMEnricher
        if search_terms is None:
            search_terms = load_search_terms(cfg.user_info_dir)
        if extractor is None or llm_enricher is None:
            try:
                prompts = load_prompts(cfg)
            except PromptError as exc:
                _log.error("startup failed — prompts: %s", exc)
                raise
            if extractor is None:
                extractor = ClaudeExtractor(cfg, prompts, run_log=run_log)

        # Step 6: Resolve parser classes
        _resolve = (
            parser_registry if parser_registry is not None else _default_registry.get
        )
        resolved: list[tuple[type[Parser], SourceEntry]] = []
        for source in cfg.sources:
            cls = _resolve(source.parser_type)
            if cls is not None:
                resolved.append((cls, source))
        native_enrich_by_type: dict[str, bool] = {
            source.parser_type: _has_native_enrich(cls) for cls, source in resolved
        }

        # Step 7: Dedup store + CardStore (wipe v1 extracts if present)
        # Load dedup store first so its URL index can seed card-store migration.
        extracts_path = cfg.seen_store_path.parent / "extracts.json"
        _wipe_extracts_if_v1(extracts_path)
        if dedup_store is None:
            try:
                dedup_store = dedup_module.load(
                    cfg.seen_store_path,
                    cooldown_days=cfg.dedup_cooldown_days,
                    run_log=run_log,
                )
            except DedupStoreError as exc:
                _log.error("startup failed — dedup store: %s", exc)
                raise
        if card_store is None:
            card_store = load_card_store(extracts_path)
            dedup_store.attach_card_store(card_store)

        # Step 8: Build shared quota wall; default LLMEnricher is constructed later
        # once the active run-scoped stores and gates exist.
        if quota_wall is None:
            quota_wall = _quota.QuotaWall()

        daily_file_path = cfg.results_dir / f"{cron_anchored_date}.md"
        daily_file = DailyResultsFile(daily_file_path)
        try:
            daily_file.ensure_initialized()
        except ResultsFileError as exc:
            _log.error("startup failed — results file: %s", exc)
            raise

        # Step 9: Enter parsers via ExitStack, start parser threads, consume outbound queue
        metrics = RunMetrics(status_display, run_log=run_log)
        classify_queue: queue.Queue[object] = queue.Queue()
        pool_collector = _PoolCollector()
        _run_started_at = datetime.now(timezone.utc)

        locations: list[Location] = [City(loc) for loc in cfg.locations]
        if cfg.include_remote:
            locations.append(Remote())

        outbound: queue.Queue[tuple[str, object]] = queue.Queue()

        with ExitStack() as stack:
            parsers_list: list[tuple[Parser, SourceEntry]] = [
                (
                    stack.enter_context(
                        cls(run_log=run_log, failures_dir=cfg.failures_path)  # type: ignore[call-arg]
                    ),
                    source,
                )
                for cls, source in resolved
            ]
            dedup_run = stack.enter_context(dedup_store.run_scope())

            # Create gate instances before starting parser threads so they can be
            # passed into the parser thread constructors.
            queries_per_parser = len(search_terms.keywords) * len(locations)
            for i, (parser, source) in enumerate(parsers_list):
                parser_id = source.parser_type
                metrics.register_parser(
                    parser_id,
                    order=2 + i * 2,
                    total_queries=queries_per_parser,
                    has_native_enrich=native_enrich_by_type.get(parser_id, False),
                )

            status_display.remove("startup")

            dedup_counters = DedupCounters(display=status_display, run_log=run_log)
            metrics.register_rows()

            freshness = FreshnessGate(
                anchored_today=anchored_today,
                max_listing_age_days=cfg.max_listing_age_days,
                dedup=dedup_run,
                display=status_display,
                run_log=run_log,
                card_store=card_store,
            )
            if llm_enricher is None:
                assert isinstance(extractor, LLMExtractor), (
                    "extractor must implement LLMExtractor (classify_relevance)"
                )
                llm_enricher = LLMEnricher(
                    extractor=extractor,
                    quota_wall=quota_wall,
                    card_store=card_store,
                    run_log=run_log,
                    failures_dir=cfg.failures_path,
                    freshness_gate=freshness,
                    dedup_store=dedup_run,
                )
            prefilter = PreFilterGate(
                blacklist=list(search_terms.negative_keywords),
                dedup=dedup_run,
                display=status_display,
                run_log=run_log,
            )
            content_gate = ContentGate(display=status_display, run_log=run_log)

            parser_states: dict[str, _ParserState] = {}
            threads: list[tuple[str, _ParserThread]] = []

            for parser, source in parsers_list:
                parser_id = source.parser_type
                worklist = [
                    ParserQuery(keyword=kw, location=loc)
                    for kw in search_terms.keywords
                    for loc in locations
                ]
                parser_states[parser_id] = _ParserState(parser_id=parser_id)
                t = _ParserThread(
                    parser_id,
                    parser,
                    worklist,
                    outbound,
                    classify_queue,
                    run_log=run_log,
                    run_state=run_state,
                    freshness=freshness,
                    prefilter=prefilter,
                    content_gate=content_gate,
                    dedup=dedup_run,
                    dedup_counters=dedup_counters,
                    pool_collector=pool_collector,
                    metrics=metrics,
                    card_store=card_store,
                )
                threads.append((parser_id, t))

            for i, (pid, t) in enumerate(threads):
                t.start()
                state = parser_states[pid]
                state.started_at = datetime.now(timezone.utc)
                state.started_monotonic = time.monotonic()
                state.last_event_monotonic = state.started_monotonic
                _log.info("parser %s started", pid)
                run_log.event("parser_" + pid, "parser started")

            dispatch_queue: queue.Queue[object] = queue.Queue()
            classify_accumulator = _ClassifyAccumulator(
                classify_queue=classify_queue,
                dispatch_queue=dispatch_queue,
                batch_size=cfg.claude_classify_batch_size,
                num_workers=cfg.claude_classify_parallelism,
                metrics=metrics,
                run_state=run_state,
            )
            classify_accumulator.start()

            classify_workers = [
                _ClassifyWorker(
                    dispatch_queue=dispatch_queue,
                    pool_collector=pool_collector,
                    llm_enricher=llm_enricher,
                    dedup_store=dedup_store,
                    metrics=metrics,
                    run_state=run_state,
                    run_log=run_log,
                    quota_wall=quota_wall,
                    worker_index=i,
                )
                for i in range(cfg.claude_classify_parallelism)
            ]
            for cw in classify_workers:
                cw.start()

            dispatcher = _OutboundDispatcher(
                parser_states=parser_states,
                metrics=metrics,
                run_log=run_log,
                failures_dir=cfg.failures_path,
            )

            parsers_remaining: set[str] = set(parser_states.keys())

            poll_s = min(stall_threshold_s, 5.0)
            while parsers_remaining:
                try:
                    pid, payload = outbound.get(timeout=poll_s)
                except queue.Empty:
                    if run_state.is_aborted:
                        continue
                    now = time.monotonic()
                    frames = sys._current_frames()
                    threads_by_name = {t.name: t for t in threading.enumerate()}
                    for stall_pid in parsers_remaining:
                        stall_state = parser_states[stall_pid]
                        age = now - stall_state.last_event_monotonic
                        if age < stall_threshold_s or stall_state.stall_logged:
                            continue
                        run_log.event(
                            "parser_" + stall_pid,
                            "stalled",
                            last_event_age_s=round(age, 1),
                        )
                        thread = threads_by_name.get(f"parser-{stall_pid}")
                        frame = (
                            frames.get(thread.ident)
                            if thread is not None and thread.ident is not None
                            else None
                        )
                        if frame is not None:
                            run_log.traceback(
                                "parser_" + stall_pid,
                                "".join(traceback.format_stack(frame)),
                            )
                        stall_state.stall_logged = True
                    continue
                current_stage.set(f"parser:{pid}")
                state = parser_states[pid]
                state.last_event_monotonic = time.monotonic()
                state.stall_logged = False

                if dispatcher.dispatch(pid, payload):
                    parsers_remaining.discard(pid)

            for _, t in threads:
                t.join()

            parsers_done_monotonic = time.monotonic()
            for pid, pstate in parser_states.items():
                run_log.summary(
                    "parser_" + pid,
                    metrics.parser_summary(
                        pid, parsers_done_monotonic, pstate.started_monotonic
                    ),
                    pstate.started_at,
                )

            freshness.emit_run_complete()
            freshness_snapshot = freshness.snapshot()
            prefilter.emit_run_complete()
            prefilter_snapshot = prefilter.snapshot()
            content_gate.emit_run_complete()
            content_snapshot = content_gate.snapshot()
            dedup_counters.emit_run_complete()
            dedup_snapshot = dedup_counters.snapshot()

            # Signal accumulator to flush partial batch and stop workers
            classify_queue.put(_NO_MORE_BATCHES)

        first_exc: BaseException | None = None
        classify_accumulator.join()
        if classify_accumulator.exc is not None:
            first_exc = classify_accumulator.exc

        for cw in classify_workers:
            cw.join()
            if first_exc is None and cw.exc is not None:
                first_exc = cw.exc

        metrics.classify_done()

        if first_exc is not None:
            raise first_exc

        # Emit per-call-site SUMMARY OF SESSION trailers
        metrics.summarize_to_parser_log(_run_started_at)

        # Step 13: Single end-of-run judge_top_n call
        candidates = pool_collector.build_candidates(card_store)
        pool_size = pool_collector.pool_size

        daily_top_5_count = 0
        if candidates and not no_judge:
            metrics.judge_started(len(candidates))
            verdicts: list[MatchVerdict] | None = None
            judge_usage = None
            assert isinstance(extractor, _LLMJudge), (
                "extractor must implement judge_top_n"
            )
            while True:
                try:
                    verdicts, judge_usage = extractor.judge_top_n(candidates)
                    break
                except ClaudeUsageLimitError as err:
                    _quota_sleep(err, run_log)
                except ExtractorError as exc:
                    _log.warning("judge_top_n failed: %s", exc)
                    run_log.event(
                        "llm_judge_top_n",
                        "error",
                        returncode=getattr(exc, "returncode", None),
                        stderr_excerpt=str(getattr(exc, "stderr", "") or "")[:200],
                        error=str(exc),
                    )
                    _write_failure(
                        stage="judge_top_n",
                        error=exc,
                        log_tail="",
                        failures_dir=cfg.failures_path,
                    )
                    metrics.judge_top_n_failed()
                    break

            if verdicts is not None and judge_usage is not None:
                for verdict in sorted(verdicts, key=lambda v: v.rank):
                    card = card_store.get(verdict.id)
                    if card is None:
                        continue
                    stub = pool_collector.get_stub(verdict.id)
                    url = stub.url if stub is not None else ""
                    try:
                        daily_file.commit(
                            rank=verdict.rank,
                            header=card.header,
                            summary=card.summary,
                            url=url,
                            body=card.body,
                        )
                    except ResultsFileError as exc:
                        _log.error("daily file append failed: %s", exc)
                        raise
                    if stub is not None:
                        dedup_store.mark_selected_by_judge(verdict.id, stub)
                    daily_top_5_count += 1
                metrics.judge_top_n_complete(judge_usage, daily_top_5_count)
                run_log.event(
                    "pipeline_orchestrator",
                    "daily_file_written",
                    path=str(daily_file_path),
                    card_count=daily_top_5_count,
                )

        elapsed_s = time.monotonic() - _start
        if run_state.degraded_reason is not None:
            metrics.set_degraded_reason(run_state.degraded_reason)

        run_log.event(
            "pipeline_orchestrator",
            "run_complete",
            classify_calls=metrics.classify_calls,
            classify_input_tokens=metrics.classify_input_tokens,
            classify_output_tokens=metrics.classify_output_tokens,
            judge_input_tokens=metrics.judge_input_tokens,
            judge_output_tokens=metrics.judge_output_tokens,
            dedup_url_hits=dedup_snapshot.dedup_url_hits,
            dedup_tuple_hits=dedup_snapshot.dedup_tuple_hits,
            dedup_run_hits=dedup_snapshot.dedup_run_hits,
            dedup_misses=dedup_snapshot.dedup_misses,
            pool_size=pool_size,
            daily_top_5_count=daily_top_5_count,
            elapsed_s=round(elapsed_s, 1),
        )

        summary = metrics.to_run_summary(
            duration_s=elapsed_s,
            prefilter=prefilter_snapshot,
            freshness=freshness_snapshot,
            content=content_snapshot,
            dedup=dedup_snapshot,
        )
        _log.info(
            "run complete: discovered=%d skipped=%d "
            "prefilter_considered=%d prefilter_passed=%d prefilter_dropped=%d "
            "prefilter_blacklist_hits=%d "
            "content_considered=%d content_passed=%d content_dropped_empty_body=%d "
            "dedup_url_hits=%d dedup_tuple_hits=%d dedup_run_hits=%d dedup_misses=%d "
            "classifier_dropped=%d written=%d "
            "enrich_failed=%d errored=%d parsers_dead=%d",
            summary.discovered,
            summary.skipped,
            summary.prefilter_considered,
            summary.prefilter_passed,
            summary.prefilter_dropped,
            summary.prefilter_blacklist_hits,
            summary.content_considered,
            summary.content_passed,
            summary.content_dropped_empty_body,
            summary.dedup_url_hits,
            summary.dedup_tuple_hits,
            summary.dedup_run_hits,
            summary.dedup_misses,
            summary.classifier_dropped,
            summary.written,
            summary.enrich_failed,
            summary.errored,
            summary.parsers_dead,
        )
        return summary
    finally:
        status_display.stop()
