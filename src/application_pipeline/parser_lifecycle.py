from __future__ import annotations

import logging
import queue
import sys
import threading
import time
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from application_pipeline._context import current_stage
from application_pipeline.classify_stage import ClassifyStageHandoff
from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import DeduplicationStore
from application_pipeline.dedup_counters import DedupCounters
from application_pipeline.extracts.card_store import CardStore
from application_pipeline.failure_report import FailureReportWriter
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.parser_intake import ParserIntake
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import (
    NotServedQuery,
    Parser,
    ParserQuery,
    PositionStub,
)
from application_pipeline.parsers.types import City, Location
from application_pipeline.pool import Pool
from application_pipeline.prefilter_gate import PreFilterGate
from application_pipeline.run_metrics import RunMetrics

_log = logging.getLogger("application_pipeline.orchestrator")

_STALL_THRESHOLD_S: float = 60.0

__all__ = [
    "ParserLifecycleCollaborators",
    "ParserLifecycleExecution",
    "ParserLifecyclePlan",
    "run_parser_lifecycle",
]


@runtime_checkable
class _ParserLifecycleRunState(Protocol):
    @property
    def is_aborted(self) -> bool: ...


class _ParserDone:
    __slots__ = ()


class _NotServedQuery:
    __slots__ = ()


@dataclass
class _ParserDead:
    exc: BaseException
    traceback_str: str


_NOT_SERVED_QUERY = _NotServedQuery()
_PARSER_DONE = _ParserDone()


class _QueryDone:
    __slots__ = ()


_QUERY_DONE = _QueryDone()


class _ParserProgress:
    __slots__ = ()


_PARSER_PROGRESS = _ParserProgress()


class _ParserThread(threading.Thread):
    """Owns Parser discovery and Parser Intake handoff for one Parser."""

    def __init__(
        self,
        parser_id: str,
        parser: Parser,
        worklist: list[ParserQuery],
        outbound: queue.Queue[tuple[str, object]],
        *,
        classify_handoff: ClassifyStageHandoff,
        run_log: RunLog,
        run_state: _ParserLifecycleRunState,
        freshness: FreshnessGate,
        prefilter: PreFilterGate,
        content_gate: ContentGate,
        dedup: DeduplicationStore,
        dedup_counters: DedupCounters,
        pool: Pool,
        metrics: RunMetrics,
        card_store: CardStore,
    ) -> None:
        super().__init__(name=f"parser-{parser_id}", daemon=True)
        self._parser_id = parser_id
        self._parser = parser
        self._worklist = worklist
        self._outbound = outbound
        self._run_log = run_log
        self._run_state = run_state
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
            pool_collector=pool,
            classify_handoff=classify_handoff,
            run_log=run_log,
            metrics=metrics,
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
                                self._outbound.put((self._parser_id, _NOT_SERVED_QUERY))
                                continue
                            if self._run_state.is_aborted:
                                break
                            self._process_position_stub(item)
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

    def _process_position_stub(self, position_stub: PositionStub) -> None:
        self._metrics.discovered(self._parser_id)
        self._parser_intake.process_position_stub(position_stub)
        self._outbound.put((self._parser_id, _PARSER_PROGRESS))


@dataclass
class _ParserState:
    parser_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_monotonic: float = field(default_factory=time.monotonic)
    last_event_monotonic: float = field(default_factory=time.monotonic)
    stall_logged: bool = False


class _OutboundDispatcher:
    def __init__(
        self,
        *,
        metrics: RunMetrics,
        run_log: RunLog,
        failure_report_writer: FailureReportWriter,
    ) -> None:
        self._metrics = metrics
        self._run_log = run_log
        self._failure_report_writer = failure_report_writer

    def dispatch(self, parser_id: str, payload: object) -> bool:
        if payload is _NOT_SERVED_QUERY:
            self._metrics.not_served_query(parser_id)
        elif payload is _QUERY_DONE:
            self._metrics.query_done(parser_id)
        elif payload is _PARSER_DONE:
            self._metrics.parser_done(parser_id)
            return True
        elif isinstance(payload, _ParserDead):
            path = self._failure_report_writer.record_parser_dead(
                parser_id=parser_id,
                error=payload.exc,
                traceback_str=payload.traceback_str,
            )
            print(f"parser {parser_id} died — failure report: {path}", file=sys.stderr)
            self._run_log.traceback("parser_" + parser_id, payload.traceback_str)
            self._metrics.parser_dead(parser_id)
            return True
        elif payload is _PARSER_PROGRESS:
            return False
        return False


@dataclass(frozen=True)
class ParserLifecycleExecution:
    parser: Parser
    parser_id: str
    classify_handoff: ClassifyStageHandoff


@dataclass(frozen=True)
class ParserLifecycleCollaborators:
    run_log: RunLog
    run_state: _ParserLifecycleRunState
    freshness: FreshnessGate
    prefilter: PreFilterGate
    content_gate: ContentGate
    dedup: DeduplicationStore
    dedup_counters: DedupCounters
    pool: Pool
    metrics: RunMetrics
    card_store: CardStore
    failure_report_writer: FailureReportWriter
    stall_threshold_s: float = _STALL_THRESHOLD_S


@dataclass(frozen=True)
class ParserLifecyclePlan:
    parsers: Sequence[ParserLifecycleExecution]
    keywords: Sequence[str]
    locations: Sequence[Location]
    collaborators: ParserLifecycleCollaborators


def run_parser_lifecycle(
    plan: ParserLifecyclePlan,
) -> None:
    """Own Parser Lifecycle without changing the Orchestrator call flow.

    This internal seam keeps Parser-thread lifecycle control, parser-private
    timing, queue protocol, Run Log query lifecycle rows, parser-dead Failure
    Report recording, watchdog Log Artifacts, and parser summary emission out of
    the Orchestrator while continuing to call Parser Intake for each Position
    Stub and to report through Run Metrics.
    """

    outbound: queue.Queue[tuple[str, object]] = queue.Queue()
    collaborators = plan.collaborators
    parser_states: dict[str, _ParserState] = {}
    threads: list[tuple[str, _ParserThread]] = []

    with collaborators.dedup.run_scope():
        for execution in plan.parsers:
            parser = execution.parser
            parser_id = execution.parser_id
            worklist = [
                ParserQuery(keyword=keyword, location=location)
                for keyword in plan.keywords
                for location in plan.locations
            ]
            parser_states[parser_id] = _ParserState(parser_id=parser_id)
            threads.append(
                (
                    parser_id,
                    _ParserThread(
                        parser_id,
                        parser,
                        worklist,
                        outbound,
                        classify_handoff=execution.classify_handoff,
                        run_log=collaborators.run_log,
                        run_state=collaborators.run_state,
                        freshness=collaborators.freshness,
                        prefilter=collaborators.prefilter,
                        content_gate=collaborators.content_gate,
                        dedup=collaborators.dedup,
                        dedup_counters=collaborators.dedup_counters,
                        pool=collaborators.pool,
                        metrics=collaborators.metrics,
                        card_store=collaborators.card_store,
                    ),
                )
            )

        for parser_id, thread in threads:
            state = parser_states[parser_id]
            state.started_at = datetime.now(timezone.utc)
            state.started_monotonic = time.monotonic()
            state.last_event_monotonic = state.started_monotonic
            _log.info("parser %s started", parser_id)
            collaborators.run_log.event("parser_" + parser_id, "parser started")
            thread.start()

        dispatcher = _OutboundDispatcher(
            metrics=collaborators.metrics,
            run_log=collaborators.run_log,
            failure_report_writer=collaborators.failure_report_writer,
        )
        parsers_remaining: set[str] = set(parser_states)
        poll_s = min(collaborators.stall_threshold_s, 5.0)

        while parsers_remaining:
            try:
                parser_id, payload = outbound.get(timeout=poll_s)
            except queue.Empty:
                if collaborators.run_state.is_aborted:
                    continue
                _log_parser_stalls(
                    parser_states=parser_states,
                    parsers_remaining=parsers_remaining,
                    threads=threads,
                    run_log=collaborators.run_log,
                    stall_threshold_s=collaborators.stall_threshold_s,
                )
                continue

            current_stage.set(f"parser:{parser_id}")
            state = parser_states[parser_id]
            state.last_event_monotonic = time.monotonic()
            state.stall_logged = False
            if dispatcher.dispatch(parser_id, payload):
                parsers_remaining.discard(parser_id)

        for _, thread in threads:
            thread.join()

        parsers_done_monotonic = time.monotonic()
        for parser_id, parser_state in parser_states.items():
            collaborators.run_log.summary(
                "parser_" + parser_id,
                collaborators.metrics.parser_summary(
                    parser_id,
                    parsers_done_monotonic,
                    parser_state.started_monotonic,
                ),
                parser_state.started_at,
            )


def _log_parser_stalls(
    *,
    parser_states: dict[str, _ParserState],
    parsers_remaining: set[str],
    threads: list[tuple[str, _ParserThread]],
    run_log: RunLog,
    stall_threshold_s: float,
) -> None:
    now = time.monotonic()
    frames = sys._current_frames()
    threads_by_name = {thread.name: thread for _, thread in threads}
    for parser_id in parsers_remaining:
        parser_state = parser_states[parser_id]
        age = now - parser_state.last_event_monotonic
        if age < stall_threshold_s or parser_state.stall_logged:
            continue
        run_log.event(
            "parser_" + parser_id,
            "stalled",
            last_event_age_s=round(age, 1),
        )
        thread = threads_by_name.get(f"parser-{parser_id}")
        frame = (
            frames.get(thread.ident)
            if thread is not None and thread.ident is not None
            else None
        )
        if frame is not None:
            run_log.traceback(
                "parser_" + parser_id,
                "".join(traceback.format_stack(frame)),
            )
        parser_state.stall_logged = True
