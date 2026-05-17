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
from application_pipeline.run_metrics import RunMetrics, RunSummary
from application_pipeline.status_display import PlainStatusDisplay, StatusDisplay
from application_pipeline.config import ConfigError, SourceEntry
from application_pipeline.dedup import DedupStoreError, DeduplicationStore
from application_pipeline.layout.types import Layout
from application_pipeline.llm import (
    ClassifyItem,
    ClaudeExtractor,
    ExtractorError,
    ExtractorUnreachableError,
    LLMExtractor,
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
    positions: list["Position"]
    item_id: str


class _NoMoreBatches:
    __slots__ = ()


_NO_MORE_BATCHES = _NoMoreBatches()


# ---------------------------------------------------------------------------
# Judge queue protocol
# ---------------------------------------------------------------------------


@dataclass
class _JudgeJob:
    position: "Position"
    item_id: str


class _NoMoreJudges:
    __slots__ = ()


_NO_MORE_JUDGES = _NoMoreJudges()


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
        judge_queue: "queue.Queue[object]",
        extractor: LLMExtractor,
        dedup_store: DeduplicationStore,
        metrics: "RunMetrics",
        run_state: _RunState,
    ) -> None:
        super().__init__(
            input_queue=classify_queue,
            sentinel=_NO_MORE_BATCHES,
            stage_name="classify",
            run_state=run_state,
        )
        self.name = "classify-worker"
        self._judge_queue = judge_queue
        self._extractor = extractor
        self._dedup_store = dedup_store
        self._metrics = metrics

    def _on_dequeue(self, item: object) -> None:
        assert isinstance(item, _ClassifyBatch)
        self._metrics.classify_batch_dequeued(len(item.positions))

    def _on_shutdown(self) -> None:
        self._judge_queue.put(_NO_MORE_JUDGES)

    def _process(self, item: object) -> None:
        assert isinstance(item, _ClassifyBatch)
        batch = item
        items = _make_classify_items(batch.positions)
        try:
            verdicts, classify_usage = self._extractor.classify_relevance_batch(items)
        except ClaudeUsageLimitError:
            self._run_state.set_degraded("usage_limit", self.name)
            return
        except ExtractorError as exc:
            _log.warning("classify_relevance_batch failed: %s", exc)
            parser_log.record(
                "classify_relevance",
                "batch_abandoned",
                batch_size=len(batch.positions),
                returncode=getattr(exc, "returncode", None),
                stderr_excerpt=str(getattr(exc, "stderr", "") or "")[:200],
                error=str(exc),
            )
            self._metrics.classify_batch_failed(len(batch.positions))
            return

        classifier_dropped = 0
        for verdict, position in zip(verdicts, batch.positions):
            if not verdict.in_domain:
                self._dedup_store.mark_off_domain(position.stub)
                classifier_dropped += 1
            else:
                self._dedup_store.mark_classified_in_domain(position.stub)
                self._metrics.judge_enqueued()
                self._judge_queue.put(
                    _JudgeJob(
                        position=position,
                        item_id=batch.item_id,
                    )
                )
        self._metrics.classify_batch_complete(
            classify_usage, len(items), classifier_dropped
        )


# ---------------------------------------------------------------------------
# Judge thread
# ---------------------------------------------------------------------------


class _JudgeThread(_QueueWorker):
    def __init__(
        self,
        *,
        judge_queue: queue.Queue[object],
        extractor: LLMExtractor,
        results_manager: "ResultsFileManager",
        dedup_store: DeduplicationStore,
        layout: "Layout",
        metrics: "RunMetrics",
        run_state: _RunState,
    ) -> None:
        super().__init__(
            input_queue=judge_queue,
            sentinel=_NO_MORE_JUDGES,
            stage_name="judge",
            run_state=run_state,
        )
        self.name = "judge-worker"
        self._extractor = extractor
        self._results_manager = results_manager
        self._dedup_store = dedup_store
        self._layout = layout
        self._metrics = metrics

    def _on_dequeue(self, item: object) -> None:
        self._metrics.judge_dequeued()

    def _process(self, item: object) -> None:
        assert isinstance(item, _JudgeJob)
        job = item
        try:
            match_verdict, judge_usage = self._extractor.judge_match(
                job.position.raw_description,
                stub_url=job.position.stub.url,
            )
            number = self._results_manager.next_position_number()
            rendered = render(job.position, match_verdict, number, self._layout)
            self._results_manager.append(rendered)
            self._dedup_store.mark_kept(job.position.stub)
            self._metrics.judge_complete(
                judge_usage, match_verdict.tier, job.position.stub.source
            )
        except ClaudeUsageLimitError:
            self._run_state.set_degraded("usage_limit", self.name)
            return
        except ExtractorError as exc:
            _log.warning("judge_match failed: %s", exc)
            parser_log.record(
                "judge_match",
                "error",
                stub_url=job.position.stub.url,
                returncode=getattr(exc, "returncode", None),
                stderr_excerpt=str(getattr(exc, "stderr", "") or "")[:200],
                error=str(exc),
            )
            self._metrics.judge_failed()


# ---------------------------------------------------------------------------
# Run Divider helpers
# ---------------------------------------------------------------------------


def _discover_release_tag() -> str | None:
    try:
        return Path("current").readlink().name or None
    except OSError:
        return None


@dataclass
class _ParserState:
    parser_id: str
    inbound: queue.Queue[object]
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_monotonic: float = field(default_factory=time.monotonic)
    last_event_monotonic: float = field(default_factory=time.monotonic)
    stall_logged: bool = False
    pending_enrich: PositionStub | None = None
    pending_judge_resume: bool = False


def _make_classify_items(batch: list[Position]) -> list[ClassifyItem]:
    return [
        ClassifyItem(
            id=str(idx),
            title=pos.stub.title,
            raw_description=pos.raw_description,
        )
        for idx, pos in enumerate(batch)
    ]


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
        metrics = RunMetrics(status_display)
        classify_buffer: list[Position] = []
        batch_size = cfg.claude_classify_batch_size
        batch_id = 0
        classify_queue: queue.Queue[object] = queue.Queue()
        judge_queue: queue.Queue[object] = queue.Queue()
        _run_started_at = datetime.now(timezone.utc)

        locations: list[Location] = [City(loc) for loc in cfg.locations]
        if cfg.include_remote:
            locations.append(Remote())

        outbound: queue.Queue[tuple[str, object]] = queue.Queue()

        with ExitStack() as stack:
            parsers_list: list[tuple[Parser, SourceEntry]] = [
                (stack.enter_context(cls()), source) for cls, source in resolved
            ]
            dedup_run = stack.enter_context(dedup_store.run_scope())

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
                metrics.register_parser(
                    pid,
                    order=2 + i,
                    total_queries=len(t._worklist),
                )

            status_display.remove("startup")

            metrics.register_rows(starting_order=2 + len(threads))

            classify_thread = _ClassifyThread(
                classify_queue=classify_queue,
                judge_queue=judge_queue,
                extractor=extractor,
                dedup_store=dedup_store,
                metrics=metrics,
                run_state=run_state,
            )
            judge_thread = _JudgeThread(
                judge_queue=judge_queue,
                extractor=extractor,
                results_manager=results_manager,
                dedup_store=dedup_store,
                layout=layout,
                metrics=metrics,
                run_state=run_state,
            )
            classify_thread.start()
            judge_thread.start()

            def _flush_classify_batch() -> None:
                nonlocal batch_id
                classify_queue.put(
                    _ClassifyBatch(
                        positions=list(classify_buffer),
                        item_id=str(batch_id),
                    )
                )
                metrics.classify_batch_enqueued(len(classify_buffer))
                batch_id += 1
                classify_buffer.clear()

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
                    metrics.discovered(pid)

                    if run_state.is_aborted:
                        state.inbound.put(_SKIP_AND_END_QUERY)
                        continue

                    result = dedup_run.is_seen(payload)
                    metrics.record_dedup(result)
                    if result == "miss":
                        state.pending_enrich = payload
                        state.pending_judge_resume = False
                        state.inbound.put(_ENRICH)
                    elif result == "judge_pending":
                        state.pending_enrich = payload
                        state.pending_judge_resume = True
                        state.inbound.put(_ENRICH)
                    else:
                        state.inbound.put(_SKIP)

                elif isinstance(payload, Position):
                    for warning in payload._warnings:
                        parser_log.record(pid, warning)
                        if warning.startswith("unparseable_date"):
                            metrics.unparseable_date(pid)
                    metrics.enriched(pid)
                    if state.pending_judge_resume:
                        state.pending_judge_resume = False
                        state.pending_enrich = None
                        metrics.judge_enqueued()
                        judge_queue.put(_JudgeJob(position=payload, item_id="resume"))
                    else:
                        verdict = prefilter.classify(payload)
                        if verdict.passes:
                            metrics.prefilter_passed(verdict)
                            classify_buffer.append(payload)
                            metrics.classify_buffered(1)
                            if len(classify_buffer) >= batch_size:
                                _flush_classify_batch()
                        else:
                            dedup_store.mark_off_domain(payload.stub)
                            metrics.prefilter_dropped(verdict)

                elif isinstance(payload, ParserError):
                    stub = state.pending_enrich
                    state.pending_enrich = None
                    state.pending_judge_resume = False
                    if stub is not None:
                        parser_log.record(
                            pid,
                            "enrich_failed",
                            stub_url=stub.url,
                            title=stub.title,
                            reason=str(payload),
                        )
                        dedup_store.mark_enrich_failed(stub)
                    metrics.enrich_failed(pid)

                elif isinstance(payload, ExternalRedirect):
                    stub = state.pending_enrich
                    state.pending_enrich = None
                    state.pending_judge_resume = False
                    if stub is not None:
                        parser_log.record(
                            pid,
                            "external_redirect",
                            stub_url=stub.url,
                            outbound=payload.outbound_url,
                        )
                        dedup_store.mark_external_redirect(stub)
                    metrics.external_redirect(pid)

                elif isinstance(payload, NotServedQuery):
                    metrics.not_served_query(pid)

                elif payload is _QUERY_DONE:
                    metrics.query_done(pid)

                elif payload is _PARSER_DONE:
                    parsers_remaining.discard(pid)
                    metrics.parser_done(pid)

                elif isinstance(payload, _ParserDead):
                    parser_log.record_traceback(pid, payload.traceback_str)
                    metrics.parser_dead(pid)
                    parsers_remaining.discard(pid)

            for _, t in threads:
                t.join()

            parsers_done_monotonic = time.monotonic()
            for pid, pstate in parser_states.items():
                parser_log.summarize(
                    pid,
                    metrics.parser_summary(
                        pid, parsers_done_monotonic, pstate.started_monotonic
                    ),
                    pstate.started_at,
                )

            # Flush residual buffer (end-of-discovery undersized batch)
            if classify_buffer:
                _flush_classify_batch()
            classify_queue.put(_NO_MORE_BATCHES)

        # Sentinel cascade: classify drains → forwards _NO_MORE_JUDGES → judge drains → exits
        classify_thread.join()
        judge_thread.join()

        if classify_thread.exc is not None:
            raise classify_thread.exc
        if judge_thread.exc is not None:
            raise judge_thread.exc

        # Emit per-call-site SUMMARY OF SESSION trailers
        metrics.summarize_to_parser_log(_run_started_at)

        # Step 13: Append Run Divider — only on successful completion
        elapsed_s = time.monotonic() - _start
        if run_state.degraded_reason is not None:
            metrics.set_degraded_reason(run_state.degraded_reason)
        divider = metrics.format_run_divider(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            tag=_discover_release_tag(),
            elapsed_s=elapsed_s,
        )
        try:
            results_manager.append(divider)
        except ResultsFileError as exc:
            _log.error("run divider append failed: %s", exc)
            raise

        summary = metrics.to_run_summary(duration_s=elapsed_s)
        _log.info(
            "run complete: discovered=%d skipped=%d "
            "prefilter_considered=%d prefilter_passed=%d prefilter_dropped=%d "
            "prefilter_whitelist_hits=%d prefilter_blacklist_hits=%d prefilter_no_hit_either=%d "
            "dedup_url_hits=%d dedup_tuple_hits=%d dedup_run_hits=%d dedup_misses=%d "
            "classifier_dropped=%d written=%d green=%d amber=%d red=%d "
            "enrich_failed=%d external_redirects=%d errored=%d parsers_dead=%d",
            summary.discovered,
            summary.skipped,
            summary.prefilter_considered,
            summary.prefilter_passed,
            summary.prefilter_dropped,
            summary.prefilter_whitelist_hits,
            summary.prefilter_blacklist_hits,
            summary.prefilter_no_hit_either,
            summary.dedup_url_hits,
            summary.dedup_tuple_hits,
            summary.dedup_run_hits,
            summary.dedup_misses,
            summary.classifier_dropped,
            summary.written,
            summary.green,
            summary.amber,
            summary.red,
            summary.enrich_failed,
            summary.external_redirects,
            summary.errored,
            summary.parsers_dead,
        )
        return summary
    finally:
        status_display.stop()
