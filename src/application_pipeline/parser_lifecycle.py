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

__all__ = ["run_parser_lifecycle"]


@runtime_checkable
class _ParserLifecycleRunState(Protocol):
    @property
    def is_aborted(self) -> bool: ...


@runtime_checkable
class _ParserLifecycleHandoffFactory(Protocol):
    def __call__(
        self, *, parser_id: str, metrics: RunMetrics
    ) -> ClassifyStageHandoff: ...


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
                                self._outbound.put((self._parser_id, item))
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
        if isinstance(payload, NotServedQuery):
            self._metrics.not_served_query(parser_id)
        elif payload is _QUERY_DONE:
            self._metrics.query_done(parser_id)
        elif payload is _PARSER_DONE:
            self._metrics.parser_done(parser_id)
            return True
        elif isinstance(payload, _ParserDead):
            self._run_log.traceback("parser_" + parser_id, payload.traceback_str)
            self._metrics.parser_dead(parser_id)
            self._failure_report_writer.record_parser_dead(
                parser_id=parser_id,
                error=payload.exc,
                traceback_str=payload.traceback_str,
            )
            return True
        return False


def run_parser_lifecycle(
    *,
    parsers: list[tuple[Parser, str]],
    keywords: list[str],
    locations: Sequence[Location],
    classify_handoff_for: _ParserLifecycleHandoffFactory,
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
    failure_report_writer: FailureReportWriter,
    stall_threshold_s: float = _STALL_THRESHOLD_S,
) -> None:
    """Own Parser Lifecycle without changing the Orchestrator call flow.

    This internal seam keeps Parser-thread lifecycle control, parser-private
    timing, queue protocol, Run Log query lifecycle rows, parser-dead Failure
    Report recording, watchdog Log Artifacts, and parser summary emission out of
    the Orchestrator while continuing to call Parser Intake for each Position
    Stub and to report through Run Metrics.
    """

    outbound: queue.Queue[tuple[str, object]] = queue.Queue()
    parser_states: dict[str, _ParserState] = {}
    threads: list[tuple[str, _ParserThread]] = []

    for parser, parser_id in parsers:
        worklist = [
            ParserQuery(keyword=keyword, location=location)
            for keyword in keywords
            for location in locations
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
                    classify_handoff=classify_handoff_for(
                        parser_id=parser_id,
                        metrics=metrics,
                    ),
                    run_log=run_log,
                    run_state=run_state,
                    freshness=freshness,
                    prefilter=prefilter,
                    content_gate=content_gate,
                    dedup=dedup,
                    dedup_counters=dedup_counters,
                    pool=pool,
                    metrics=metrics,
                    card_store=card_store,
                ),
            )
        )

    for parser_id, thread in threads:
        thread.start()
        state = parser_states[parser_id]
        state.started_at = datetime.now(timezone.utc)
        state.started_monotonic = time.monotonic()
        state.last_event_monotonic = state.started_monotonic
        _log.info("parser %s started", parser_id)
        run_log.event("parser_" + parser_id, "parser started")

    dispatcher = _OutboundDispatcher(
        metrics=metrics,
        run_log=run_log,
        failure_report_writer=failure_report_writer,
    )
    parsers_remaining: set[str] = set(parser_states)
    poll_s = min(stall_threshold_s, 5.0)

    while parsers_remaining:
        try:
            parser_id, payload = outbound.get(timeout=poll_s)
        except queue.Empty:
            if run_state.is_aborted:
                continue
            _log_parser_stalls(
                parser_states=parser_states,
                parsers_remaining=parsers_remaining,
                threads=threads,
                run_log=run_log,
                stall_threshold_s=stall_threshold_s,
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
        run_log.summary(
            "parser_" + parser_id,
            metrics.parser_summary(
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
