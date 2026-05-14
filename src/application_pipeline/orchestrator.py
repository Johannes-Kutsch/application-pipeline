from __future__ import annotations

import logging
import queue
import sys
import threading
import time
import traceback
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from application_pipeline import config as config_module
from application_pipeline import dedup as dedup_module
from application_pipeline import layout as layout_module
from application_pipeline import parser_log
from application_pipeline._context import current_stage
from application_pipeline.status_display import PlainStatusDisplay, StatusDisplay
from application_pipeline.config import ConfigError, SourceEntry
from application_pipeline.dedup import DedupStoreError, DeduplicationStore
from application_pipeline.language import LanguageResolution, resolve_language
from application_pipeline.layout.types import Layout
from application_pipeline.llm import (
    ClassifyItem,
    ClaudeExtractor,
    ExtractorError,
    ExtractorUnreachableError,
    LLMExtractor,
    MatchTier,
)
from application_pipeline.llm.claude_cli import ClaudeUsageLimitError
from application_pipeline.parsers import (
    ExternalRedirect,
    NotServedQuery,
    Parser,
    ParserQuery,
    Position,
    PositionStub,
)
from application_pipeline.parsers.types import City, Location, Remote
from application_pipeline.parsers import registry as _default_registry
from application_pipeline.parsers.errors import ParserError
from application_pipeline.prefilter import DomainPreFilter
from application_pipeline.prompts import PromptError, load_prompts
from application_pipeline.renderer import render
from application_pipeline.results import ResultsFileError, ResultsFileManager

_log = logging.getLogger(__name__)

_DISCOVER_SHORT_CIRCUIT_FALLBACK = 50
_STALL_THRESHOLD_S: float = 60.0


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


class _Enrich:
    __slots__ = ()


class _Skip:
    __slots__ = ()


class _SkipAndEndQuery:
    __slots__ = ()


_PARSER_DONE = _ParserDone()
_ENRICH = _Enrich()
_SKIP = _Skip()
_SKIP_AND_END_QUERY = _SkipAndEndQuery()


class _QueryDone:
    __slots__ = ()


_QUERY_DONE = _QueryDone()


# ---------------------------------------------------------------------------
# Classify queue protocol
# ---------------------------------------------------------------------------


@dataclass
class _ClassifyBatch:
    language: str
    positions: list[tuple["Position", "LanguageResolution"]]
    item_id: str
    remaining_en: int
    remaining_de: int


class _NoMoreBatches:
    __slots__ = ()


_NO_MORE_BATCHES = _NoMoreBatches()


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
                self._process(item)
        except BaseException as exc:
            self.exc = exc
            self._run_state.set_aborted(exc)

    def _process(self, item: object) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Parser thread — pure producer
# ---------------------------------------------------------------------------


class _ParserThread(threading.Thread):
    def __init__(
        self,
        parser_id: str,
        parser: Parser,
        worklist: list[ParserQuery],
        outbound: queue.Queue[tuple[str, object]],
        inbound: queue.Queue[object],
    ) -> None:
        super().__init__(name=f"parser-{parser_id}", daemon=True)
        self._parser_id = parser_id
        self._parser = parser
        self._worklist = worklist
        self._outbound = outbound
        self._inbound = inbound

    def run(self) -> None:
        try:
            for query in self._worklist:
                location_str = (
                    query.location.name
                    if isinstance(query.location, City)
                    else "Remote"
                )
                parser_log.record(
                    self._parser_id,
                    "query_started",
                    keyword=query.keyword,
                    location=location_str,
                )
                try:
                    gen = iter(self._parser.discover(query))
                    try:
                        for item in gen:
                            self._outbound.put((self._parser_id, item))
                            if isinstance(item, NotServedQuery):
                                continue  # fire-and-forget; orchestrator counts, no reply
                            decision = self._inbound.get()
                            if decision is _ENRICH:
                                try:
                                    position = self._parser.enrich(item)
                                    self._outbound.put((self._parser_id, position))
                                except ParserError as exc:
                                    self._outbound.put((self._parser_id, exc))
                            elif decision is _SKIP_AND_END_QUERY:
                                break
                            # else: _SKIP — continue to next stub
                    finally:
                        close = getattr(gen, "close", None)
                        if close is not None:
                            close()
                    self._outbound.put((self._parser_id, _QUERY_DONE))
                finally:
                    parser_log.record(
                        self._parser_id,
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


# ---------------------------------------------------------------------------
# Classify thread
# ---------------------------------------------------------------------------


class _ClassifyThread(_QueueWorker):
    def __init__(
        self,
        *,
        classify_queue: queue.Queue[object],
        survivors_queue: "queue.Queue[tuple[Position, LanguageResolution]]",
        extractor: LLMExtractor,
        dedup_store: DeduplicationStore,
        status_display: "StatusDisplay",
        total_batches: int,
        classify_stats: "_ClassifyStats",
        run_state: _RunState,
    ) -> None:
        super().__init__(
            input_queue=classify_queue,
            sentinel=_NO_MORE_BATCHES,
            stage_name="classify",
            run_state=run_state,
        )
        self.name = "classify-worker"
        self._survivors_queue = survivors_queue
        self._extractor = extractor
        self._dedup_store = dedup_store
        self._status_display = status_display
        self._total_batches = total_batches
        self.classify_stats = classify_stats

    def _process(self, item: object) -> None:
        assert isinstance(item, _ClassifyBatch)
        batch = item
        items = _make_classify_items(batch.positions)
        try:
            _t0 = time.monotonic()
            verdicts, classify_usage = self._extractor.classify_relevance_batch(
                batch.language, items
            )
            self.classify_stats.classify_total_s += time.monotonic() - _t0
            self.classify_stats.classify_calls += 1
            self.classify_stats.classify_items += len(items)
            self.classify_stats.classify_input_tokens += classify_usage.input_tokens
            self.classify_stats.classify_output_tokens += classify_usage.output_tokens
            self.classify_stats.classify_cache_read_tokens += (
                classify_usage.cache_read_tokens
            )
            self.classify_stats.classify_cost_usd += classify_usage.cost_usd
        except ClaudeUsageLimitError:
            raise
        except ExtractorError as exc:
            _log.warning("classify_relevance_batch failed: %s", exc)
            parser_log.record(
                "classify_relevance",
                "batch_error",
                language=batch.language,
                batch_size=len(batch.positions),
                error=str(exc),
            )
            self.classify_stats.classify_failed += 1
            self.classify_stats.items_errored += len(batch.positions)
            return

        self._status_display.update_body(
            "classify_relevance",
            body=self.classify_stats.body(
                self._total_batches, batch.remaining_en, batch.remaining_de
            ),
        )

        for verdict, (position, resolution) in zip(verdicts, batch.positions):
            if not verdict.in_domain:
                self._dedup_store.mark_off_domain(position.stub)
                self.classify_stats.classifier_dropped += 1
            else:
                self._survivors_queue.put((position, resolution))


# ---------------------------------------------------------------------------
# Run Divider helpers
# ---------------------------------------------------------------------------


def _discover_release_tag() -> str | None:
    try:
        return Path("current").readlink().name or None
    except OSError:
        return None


def _format_run_divider(
    *,
    timestamp: str,
    tag: str | None,
    sources: dict[str, int],
    kept: int,
    errors: int,
    classify_calls: int,
    classify_items: int,
    classify_total_s: float,
    judge_calls: int,
    judge_total_s: float,
    claude_input_tokens: int,
    claude_output_tokens: int,
    claude_cache_read_tokens: int,
    claude_cost_usd: float,
    elapsed_s: float,
) -> str:
    parts = [f"run {timestamp}"]
    if tag is not None:
        parts.append(f"tag={tag}")
    if sources:
        sources_str = ",".join(f"{k}:{v}" for k, v in sources.items())
        parts.append(f"sources={sources_str}")
    parts.extend(
        [
            f"kept={kept}",
            f"errors={errors}",
            f"classify_calls={classify_calls}",
            f"classify_items={classify_items}",
            f"classify_total_s={classify_total_s:.1f}",
            f"judge_calls={judge_calls}",
            f"judge_total_s={judge_total_s:.1f}",
            f"claude_input_tokens={claude_input_tokens}",
            f"claude_output_tokens={claude_output_tokens}",
            f"claude_cache_read_tokens={claude_cache_read_tokens}",
            f"claude_cost_usd={claude_cost_usd:.6f}",
            f"elapsed_s={elapsed_s:.1f}",
        ]
    )
    return f"<!-- {' '.join(parts)} -->\n"


# ---------------------------------------------------------------------------
# RunSummary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSummary:
    discovered: int = 0
    skipped: int = 0
    prefilter_considered: int = 0
    prefilter_passed: int = 0
    prefilter_dropped: int = 0
    prefilter_whitelist_hits: int = 0
    prefilter_blacklist_hits: int = 0
    prefilter_no_hit_either: int = 0
    dedup_url_hits: int = 0
    dedup_tuple_hits: int = 0
    dedup_misses: int = 0
    classifier_dropped: int = 0
    written: int = 0
    green: int = 0
    amber: int = 0
    red: int = 0
    enrich_failed: int = 0
    external_redirects: int = 0
    errored: int = 0
    parsers_dead: int = 0
    classify_items: int = 0
    claude_input_tokens: int = 0
    claude_output_tokens: int = 0
    claude_cache_read_tokens: int = 0
    claude_cost_usd: float = 0.0
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Batch classify + pipeline helpers
# ---------------------------------------------------------------------------


@dataclass
class _ClassifyStats:
    classify_calls: int = 0
    classify_items: int = 0
    classify_failed: int = 0
    classify_total_s: float = 0.0
    classify_input_tokens: int = 0
    classify_output_tokens: int = 0
    classify_cache_read_tokens: int = 0
    classify_cost_usd: float = 0.0
    classifier_dropped: int = 0
    items_errored: int = 0

    def body(self, total_batches: int, remaining_en: int, remaining_de: int) -> str:
        queue_total = remaining_en + remaining_de
        return (
            f"{self.classify_calls}/{total_batches} batches done"
            f" · {queue_total} items in queue ({remaining_en} en / {remaining_de} de)"
        )


@dataclass
class _JudgeStats:
    judge_calls: int = 0
    judge_failed: int = 0
    judge_total_s: float = 0.0
    judge_input_tokens: int = 0
    judge_output_tokens: int = 0
    judge_cache_read_tokens: int = 0
    judge_cost_usd: float = 0.0
    written: int = 0
    green: int = 0
    amber: int = 0
    red: int = 0
    written_per_source: dict[str, int] = field(default_factory=dict)
    errored: int = 0

    def body(self) -> str:
        total_judged = self.judge_calls + self.judge_failed
        return f"{self.judge_calls}/{total_judged} judgments · green={self.green} amber={self.amber} red={self.red}"


@dataclass
class _ParserState:
    parser_id: str
    inbound: queue.Queue[object]
    threshold: int
    total_queries: int
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_monotonic: float = field(default_factory=time.monotonic)
    last_event_monotonic: float = field(default_factory=time.monotonic)
    stall_logged: bool = False
    pending_enrich: PositionStub | None = None
    consecutive_url_hits: int = 0
    discovered: int = 0
    enrich_failed: int = 0
    external_redirects: int = 0
    not_served: int = 0
    parsers_dead: int = 0
    unparseable_dates: int = 0
    enriched: int = 0
    queries_done: int = 0

    def summary_dict(self, end_monotonic: float) -> dict[str, int | float]:
        return {
            "discovered": self.discovered,
            "enrich_failed": self.enrich_failed,
            "external_redirects": self.external_redirects,
            "not_served_queries": self.not_served,
            "parsers_dead": self.parsers_dead,
            "unparseable_dates": self.unparseable_dates,
            "duration": round(end_monotonic - self.started_monotonic, 1),
        }


def _make_classify_items(
    batch: list[tuple[Position, LanguageResolution]],
) -> list[ClassifyItem]:
    return [
        ClassifyItem(
            id=str(idx),
            title=pos.stub.title,
            raw_description=pos.raw_description,
        )
        for idx, (pos, _) in enumerate(batch)
    ]


def _make_parser_body(
    queries_done: int, total_queries: int, stubs: int, enriched: int
) -> str:
    return (
        f"{queries_done}/{total_queries} queries · {stubs} stubs · {enriched} enriched"
    )


def run(
    config_path: Path,
    *,
    extractor: LLMExtractor | None = None,
    prefilter: DomainPreFilter | None = None,
    parser_registry: Callable[[str], type[Parser] | None] | None = None,
    dedup_store: DeduplicationStore | None = None,
    results_manager: ResultsFileManager | None = None,
    layout: Layout | None = None,
    status_display: StatusDisplay | None = None,
    stall_threshold_s: float = _STALL_THRESHOLD_S,
) -> RunSummary:
    if status_display is None:
        status_display = PlainStatusDisplay()

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

        # Steps 2-4: Load prompts, build extractor, prewarm
        if extractor is None:
            try:
                prompts = load_prompts(cfg)
            except PromptError as exc:
                _log.error("startup failed — prompts: %s", exc)
                raise
            extractor = ClaudeExtractor(cfg, prompts)

        status_display.update_body("startup", body="prewarming claude cli")
        try:
            extractor.prewarm()
        except ExtractorUnreachableError as exc:
            _log.error("startup failed — extractor unreachable: %s", exc)
            raise

        # Step 5: Domain Pre-Filter
        if prefilter is None:
            prefilter = DomainPreFilter(
                inclusion_keywords=cfg.inclusion_keywords,
                negative_keywords=cfg.negative_keywords,
                skills=cfg.skills,
            )

        # Step 6: Resolve parser classes; unknown types are skipped (registry logs WARNING)
        _resolve = (
            parser_registry if parser_registry is not None else _default_registry.get
        )
        resolved: list[tuple[type[Parser], SourceEntry]] = []
        for source in cfg.sources:
            cls = _resolve(source.parser_type)
            if cls is not None:
                resolved.append((cls, source))

        # Step 7: Dedup store
        if dedup_store is None:
            try:
                dedup_store = dedup_module.load(cfg.seen_store_path)
            except DedupStoreError as exc:
                _log.error("startup failed — dedup store: %s", exc)
                raise

        # Step 8: Layout + Results manager + initialization
        if layout is None:
            if cfg.layout is not None:
                layout = layout_module.load(cfg.layout)
            else:
                layout = layout_module.default()

        if results_manager is None:
            results_manager = ResultsFileManager(
                Path("results/current.md"), layout.file_header
            )
        try:
            results_manager.ensure_initialized()
        except ResultsFileError as exc:
            _log.error("startup failed — results file: %s", exc)
            raise

        # Step 9: Enter parsers via ExitStack, start parser threads, consume outbound queue
        discovered = 0
        skipped = 0
        dedup_url_hits = 0
        dedup_tuple_hits = 0
        dedup_misses = 0
        prefilter_considered = 0
        prefilter_passed = 0
        prefilter_dropped = 0
        prefilter_whitelist_hits = 0
        prefilter_blacklist_hits = 0
        prefilter_no_hit_either = 0
        enrich_failed = 0
        external_redirects = 0
        parsers_dead = 0
        survivors: list[tuple[Position, LanguageResolution]] = []
        language_anomalies = 0
        _run_started_at = datetime.now(timezone.utc)

        locations: list[Location] = [City(loc) for loc in cfg.locations]
        if cfg.include_remote:
            locations.append(Remote())

        outbound: queue.Queue[tuple[str, object]] = queue.Queue()

        with ExitStack() as stack:
            parsers_list: list[tuple[Parser, SourceEntry]] = [
                (stack.enter_context(cls()), source) for cls, source in resolved
            ]

            parser_states: dict[str, _ParserState] = {}
            threads: list[tuple[str, _ParserThread]] = []

            for parser, source in parsers_list:
                parser_id = source.parser_type
                inbound: queue.Queue[object] = queue.Queue()
                worklist = [
                    ParserQuery(
                        keyword=kw, location=loc, max_results=source.max_results
                    )
                    for kw in cfg.keywords
                    for loc in locations
                ]
                parser_states[parser_id] = _ParserState(
                    parser_id=parser_id,
                    inbound=inbound,
                    threshold=getattr(
                        parser, "page_size", _DISCOVER_SHORT_CIRCUIT_FALLBACK
                    ),
                    total_queries=len(worklist),
                )
                t = _ParserThread(parser_id, parser, worklist, outbound, inbound)
                threads.append((parser_id, t))

            for i, (pid, t) in enumerate(threads):
                t.start()
                state = parser_states[pid]
                state.started_at = datetime.now(timezone.utc)
                state.started_monotonic = time.monotonic()
                state.last_event_monotonic = state.started_monotonic
                _log.info("parser %s started", pid)
                parser_log.record(pid, "parser started")
                status_display.register(
                    pid,
                    order=2 + i,
                    phase="running",
                    body=_make_parser_body(0, state.total_queries, 0, 0),
                )

            status_display.remove("startup")

            status_display.register("dedup", order=2 + len(threads), phase="running")
            status_display.register(
                "prefilter", order=3 + len(threads), phase="running"
            )
            status_display.register(
                "classify_relevance", order=4 + len(threads), phase="running"
            )
            status_display.register(
                "judge_match", order=5 + len(threads), phase="running"
            )

            parsers_remaining: set[str] = set(parser_states.keys())

            def _update_parser_row(pid: str, suffix: str = "") -> None:
                s = parser_states[pid]
                status_display.update_body(
                    pid,
                    body=_make_parser_body(
                        s.queries_done,
                        s.total_queries,
                        s.discovered,
                        s.enriched,
                    )
                    + suffix,
                )

            poll_s = min(stall_threshold_s, 5.0)
            while parsers_remaining:
                try:
                    pid, payload = outbound.get(timeout=poll_s)
                except queue.Empty:
                    now = time.monotonic()
                    frames = sys._current_frames()
                    threads_by_name = {t.name: t for t in threading.enumerate()}
                    for stall_pid in parsers_remaining:
                        stall_state = parser_states[stall_pid]
                        age = now - stall_state.last_event_monotonic
                        if age < stall_threshold_s or stall_state.stall_logged:
                            continue
                        parser_log.record(
                            stall_pid, "stalled", last_event_age_s=round(age, 1)
                        )
                        thread = threads_by_name.get(f"parser-{stall_pid}")
                        frame = (
                            frames.get(thread.ident)
                            if thread is not None and thread.ident is not None
                            else None
                        )
                        if frame is not None:
                            parser_log.record_traceback(
                                stall_pid, "".join(traceback.format_stack(frame))
                            )
                        stall_state.stall_logged = True
                    continue
                current_stage.set(f"parser:{pid}")
                state = parser_states[pid]
                state.last_event_monotonic = time.monotonic()
                state.stall_logged = False

                if isinstance(payload, PositionStub):
                    discovered += 1
                    state.discovered += 1
                    seen_result = dedup_store.is_seen(payload)

                    if seen_result == "miss":
                        dedup_misses += 1
                        state.consecutive_url_hits = 0
                        state.pending_enrich = payload
                        state.inbound.put(_ENRICH)
                    elif seen_result == "url_hit":
                        dedup_url_hits += 1
                        state.consecutive_url_hits += 1
                        skipped += 1
                        if state.consecutive_url_hits >= state.threshold:
                            state.consecutive_url_hits = 0
                            state.inbound.put(_SKIP_AND_END_QUERY)
                        else:
                            state.inbound.put(_SKIP)
                    else:  # tuple_hit
                        dedup_tuple_hits += 1
                        state.consecutive_url_hits = 0
                        skipped += 1
                        state.inbound.put(_SKIP)
                    status_display.update_body(
                        "pipeline",
                        body=f"discovered={discovered} written=0 errors={enrich_failed + parsers_dead}",
                    )
                    status_display.update_body(
                        "dedup",
                        body=f"url_hits={dedup_url_hits} tuple_hits={dedup_tuple_hits} misses={dedup_misses}",
                    )
                    _update_parser_row(pid)

                elif isinstance(payload, Position):
                    for warning in payload._warnings:
                        parser_log.record(pid, warning)
                        if warning.startswith("unparseable_date"):
                            state.unparseable_dates += 1
                    state.enriched += 1
                    resolution = resolve_language(payload)
                    if resolution.detected not in ("de", "en"):
                        parser_log.record(
                            "language",
                            "anomaly",
                            stub_url=payload.stub.url,
                            title=payload.stub.title,
                            source_parser=payload.stub.source,
                            company=payload.stub.company,
                            location=payload.stub.location,
                            language=resolution.effective,
                            detected=resolution.detected,
                            detection_source=resolution.source,
                        )
                        language_anomalies += 1
                    verdict = prefilter.classify(payload)
                    prefilter_considered += 1
                    if verdict.whitelist_hit:
                        prefilter_whitelist_hits += 1
                    if verdict.blacklist_hit:
                        prefilter_blacklist_hits += 1
                    if not verdict.whitelist_hit and not verdict.blacklist_hit:
                        prefilter_no_hit_either += 1
                    if verdict.passes:
                        prefilter_passed += 1
                        survivors.append((payload, resolution))
                    else:
                        dedup_store.mark_off_domain(payload.stub)
                        prefilter_dropped += 1
                    status_display.update_body(
                        "prefilter",
                        body=f"considered={prefilter_considered} passed={prefilter_passed} dropped={prefilter_dropped} (wl={prefilter_whitelist_hits} bl={prefilter_blacklist_hits})",
                    )
                    _update_parser_row(pid)

                elif isinstance(payload, ParserError):
                    stub = state.pending_enrich
                    state.pending_enrich = None
                    if stub is not None:
                        parser_log.record(
                            pid,
                            "enrich_failed",
                            stub_url=stub.url,
                            title=stub.title,
                            reason=str(payload),
                        )
                        dedup_store.mark_enrich_failed(stub)
                    enrich_failed += 1
                    state.enrich_failed += 1
                    status_display.update_body(
                        "pipeline",
                        body=f"discovered={discovered} written=0 errors={enrich_failed + parsers_dead}",
                    )

                elif isinstance(payload, ExternalRedirect):
                    stub = state.pending_enrich
                    state.pending_enrich = None
                    if stub is not None:
                        parser_log.record(
                            pid,
                            "external_redirect",
                            stub_url=stub.url,
                            outbound=payload.outbound_url,
                        )
                        dedup_store.mark_external_redirect(stub)
                    external_redirects += 1
                    state.external_redirects += 1

                elif isinstance(payload, NotServedQuery):
                    state.not_served += 1

                elif payload is _QUERY_DONE:
                    state.queries_done += 1
                    _update_parser_row(pid)

                elif payload is _PARSER_DONE:
                    parsers_remaining.discard(pid)
                    _update_parser_row(pid, " · done")

                elif isinstance(payload, _ParserDead):
                    parser_log.record_traceback(pid, payload.traceback_str)
                    parsers_dead += 1
                    state.parsers_dead += 1
                    parsers_remaining.discard(pid)
                    _update_parser_row(pid, " · dead")
                    status_display.update_body(
                        "pipeline",
                        body=f"discovered={discovered} written=0 errors={enrich_failed + parsers_dead}",
                    )

            for _, t in threads:
                t.join()

            parsers_done_monotonic = time.monotonic()
            for pid, pstate in parser_states.items():
                parser_log.summarize(
                    pid,
                    pstate.summary_dict(parsers_done_monotonic),
                    pstate.started_at,
                )
            parser_log.summarize(
                "language",
                {"anomalies": language_anomalies},
                _run_started_at,
            )

        # Steps 10-12: Classify on dedicated thread; judge survivors on main thread
        # Per-language buffers: de → de; en/other/unknown → en
        de_buffer: list[tuple[Position, LanguageResolution]] = []
        en_buffer: list[tuple[Position, LanguageResolution]] = []
        for position, resolution in survivors:
            if resolution.effective == "de":
                de_buffer.append((position, resolution))
            else:
                en_buffer.append((position, resolution))

        batch_size = cfg.claude_classify_batch_size
        classify_stats = _ClassifyStats()
        judge_stats = _JudgeStats()
        total_batches = sum(
            (len(buf) + batch_size - 1) // batch_size for buf in (de_buffer, en_buffer)
        )

        classify_queue: queue.Queue[object] = queue.Queue()
        survivors_queue: queue.Queue[tuple[Position, LanguageResolution]] = (
            queue.Queue()
        )
        classify_thread = _ClassifyThread(
            classify_queue=classify_queue,
            survivors_queue=survivors_queue,
            extractor=extractor,
            dedup_store=dedup_store,
            status_display=status_display,
            total_batches=total_batches,
            classify_stats=classify_stats,
            run_state=run_state,
        )
        classify_thread.start()

        batch_id = 0
        for lang_str, lang_buffer in [("de", de_buffer), ("en", en_buffer)]:
            for i in range(0, len(lang_buffer), batch_size):
                batch = lang_buffer[i : i + batch_size]
                sent_in_lang = i + len(batch)
                rem_de = len(de_buffer) - sent_in_lang if lang_str == "de" else 0
                rem_en = (
                    len(en_buffer) - sent_in_lang
                    if lang_str == "en"
                    else len(en_buffer)
                )
                classify_queue.put(
                    _ClassifyBatch(
                        language=lang_str,
                        positions=batch,
                        item_id=str(batch_id),
                        remaining_en=rem_en,
                        remaining_de=rem_de,
                    )
                )
                batch_id += 1
        classify_queue.put(_NO_MORE_BATCHES)
        classify_thread.join()

        if classify_thread.exc is not None:
            raise classify_thread.exc

        judge_stats.errored += classify_stats.items_errored

        # Drain survivors and judge each synchronously on main thread
        try:
            while True:
                try:
                    position, resolution = survivors_queue.get_nowait()
                except queue.Empty:
                    break

                try:
                    _t0 = time.monotonic()
                    match_verdict, judge_usage = extractor.judge_match(
                        resolution.effective, position.raw_description
                    )
                    judge_stats.judge_total_s += time.monotonic() - _t0
                    judge_stats.judge_calls += 1
                    judge_stats.judge_input_tokens += judge_usage.input_tokens
                    judge_stats.judge_output_tokens += judge_usage.output_tokens
                    judge_stats.judge_cache_read_tokens += judge_usage.cache_read_tokens
                    judge_stats.judge_cost_usd += judge_usage.cost_usd
                except ClaudeUsageLimitError:
                    raise
                except ExtractorError as exc:
                    _log.warning("judge_match failed: %s", exc)
                    parser_log.record(
                        "judge_match",
                        "error",
                        stub_url=position.stub.url,
                        error=str(exc),
                    )
                    judge_stats.judge_failed += 1
                    judge_stats.errored += 1
                    continue

                current_stage.set("results_write")
                number = results_manager.next_position_number()
                rendered = render(position, match_verdict, number, layout)
                results_manager.append(rendered)
                dedup_store.mark_kept(position.stub)
                judge_stats.written += 1
                src = position.stub.source
                judge_stats.written_per_source[src] = (
                    judge_stats.written_per_source.get(src, 0) + 1
                )
                if match_verdict.tier == MatchTier.green:
                    judge_stats.green += 1
                elif match_verdict.tier == MatchTier.amber:
                    judge_stats.amber += 1
                else:
                    judge_stats.red += 1

                status_display.update_body(
                    "judge_match",
                    body=judge_stats.body(),
                )
                status_display.update_body(
                    "pipeline",
                    body=f"discovered={discovered} written={judge_stats.written} errors={enrich_failed + parsers_dead + judge_stats.errored}",
                )
        except ClaudeUsageLimitError as exc:
            run_state.set_aborted(exc)
            raise

        # Emit per-call-site SUMMARY OF SESSION trailers
        parser_log.summarize(
            "classify_relevance",
            {
                "batches_sent": classify_stats.classify_calls,
                "items_classified": classify_stats.classify_items,
                "in_domain": classify_stats.classify_items
                - classify_stats.classifier_dropped,
                "off_domain": classify_stats.classifier_dropped,
                "batches_failed": classify_stats.classify_failed,
                "input_tokens": classify_stats.classify_input_tokens,
                "output_tokens": classify_stats.classify_output_tokens,
                "cache_read_tokens": classify_stats.classify_cache_read_tokens,
                "cost_usd": round(classify_stats.classify_cost_usd, 6),
                "duration_s": round(classify_stats.classify_total_s, 1),
            },
            _run_started_at,
        )
        parser_log.summarize(
            "judge_match",
            {
                "judges_sent": judge_stats.judge_calls,
                "judges_failed": judge_stats.judge_failed,
                "green": judge_stats.green,
                "amber": judge_stats.amber,
                "red": judge_stats.red,
                "input_tokens": judge_stats.judge_input_tokens,
                "output_tokens": judge_stats.judge_output_tokens,
                "cache_read_tokens": judge_stats.judge_cache_read_tokens,
                "cost_usd": round(judge_stats.judge_cost_usd, 6),
                "duration_s": round(judge_stats.judge_total_s, 1),
            },
            _run_started_at,
        )

        # Step 13: Append Run Divider — only on successful completion
        elapsed_s = time.monotonic() - _start
        claude_input_tokens = (
            classify_stats.classify_input_tokens + judge_stats.judge_input_tokens
        )
        claude_output_tokens = (
            classify_stats.classify_output_tokens + judge_stats.judge_output_tokens
        )
        claude_cache_read_tokens = (
            classify_stats.classify_cache_read_tokens
            + judge_stats.judge_cache_read_tokens
        )
        claude_cost_usd = classify_stats.classify_cost_usd + judge_stats.judge_cost_usd
        divider = _format_run_divider(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            tag=_discover_release_tag(),
            sources=judge_stats.written_per_source,
            kept=judge_stats.written,
            errors=judge_stats.errored,
            classify_calls=classify_stats.classify_calls,
            classify_items=classify_stats.classify_items,
            classify_total_s=classify_stats.classify_total_s,
            judge_calls=judge_stats.judge_calls,
            judge_total_s=judge_stats.judge_total_s,
            claude_input_tokens=claude_input_tokens,
            claude_output_tokens=claude_output_tokens,
            claude_cache_read_tokens=claude_cache_read_tokens,
            claude_cost_usd=claude_cost_usd,
            elapsed_s=elapsed_s,
        )
        try:
            results_manager.append(divider)
        except ResultsFileError as exc:
            _log.error("run divider append failed: %s", exc)
            raise

        _log.info(
            "run complete: discovered=%d skipped=%d "
            "prefilter_considered=%d prefilter_passed=%d prefilter_dropped=%d "
            "prefilter_whitelist_hits=%d prefilter_blacklist_hits=%d prefilter_no_hit_either=%d "
            "dedup_url_hits=%d dedup_tuple_hits=%d dedup_misses=%d "
            "classifier_dropped=%d written=%d green=%d amber=%d red=%d "
            "enrich_failed=%d external_redirects=%d errored=%d parsers_dead=%d",
            discovered,
            skipped,
            prefilter_considered,
            prefilter_passed,
            prefilter_dropped,
            prefilter_whitelist_hits,
            prefilter_blacklist_hits,
            prefilter_no_hit_either,
            dedup_url_hits,
            dedup_tuple_hits,
            dedup_misses,
            classify_stats.classifier_dropped,
            judge_stats.written,
            judge_stats.green,
            judge_stats.amber,
            judge_stats.red,
            enrich_failed,
            external_redirects,
            judge_stats.errored,
            parsers_dead,
        )

        return RunSummary(
            duration_seconds=elapsed_s,
            discovered=discovered,
            skipped=skipped,
            prefilter_considered=prefilter_considered,
            prefilter_passed=prefilter_passed,
            prefilter_dropped=prefilter_dropped,
            prefilter_whitelist_hits=prefilter_whitelist_hits,
            prefilter_blacklist_hits=prefilter_blacklist_hits,
            prefilter_no_hit_either=prefilter_no_hit_either,
            dedup_url_hits=dedup_url_hits,
            dedup_tuple_hits=dedup_tuple_hits,
            dedup_misses=dedup_misses,
            classifier_dropped=classify_stats.classifier_dropped,
            written=judge_stats.written,
            green=judge_stats.green,
            amber=judge_stats.amber,
            red=judge_stats.red,
            enrich_failed=enrich_failed,
            external_redirects=external_redirects,
            errored=judge_stats.errored,
            parsers_dead=parsers_dead,
            classify_items=classify_stats.classify_items,
            claude_input_tokens=claude_input_tokens,
            claude_output_tokens=claude_output_tokens,
            claude_cache_read_tokens=claude_cache_read_tokens,
            claude_cost_usd=claude_cost_usd,
        )
    finally:
        status_display.stop()
