from __future__ import annotations

import contextvars
import logging
import queue
import threading
import time
import traceback
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from application_pipeline import config as config_module
from application_pipeline import dedup as dedup_module
from application_pipeline import layout as layout_module
from application_pipeline import parser_log
from application_pipeline.config import ConfigError, SourceEntry
from application_pipeline.dedup import DedupStoreError, DeduplicationStore
from application_pipeline.language import LanguageResolution, resolve_language
from application_pipeline.layout.types import Layout
from application_pipeline.llm import (
    ExtractorError,
    ExtractorUnreachableError,
    LLMExtractor,
    MatchTier,
    MatchVerdict,
    OllamaExtractor,
)
from application_pipeline.parsers import (
    ExternalRedirect,
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

current_stage: contextvars.ContextVar[str] = contextvars.ContextVar(
    "application_pipeline.current_stage", default="orchestrator"
)

_DISCOVER_SHORT_CIRCUIT_FALLBACK = 50


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
                gen = iter(self._parser.discover(query))
                try:
                    for stub in gen:
                        self._outbound.put((self._parser_id, stub))
                        decision = self._inbound.get()
                        if decision is _ENRICH:
                            try:
                                position = self._parser.enrich(stub)
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
        except BaseException as exc:
            self._outbound.put(
                (self._parser_id, _ParserDead(exc, traceback.format_exc()))
            )
        else:
            self._outbound.put((self._parser_id, _PARSER_DONE))


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
    classify_total_s: float,
    judge_calls: int,
    judge_total_s: float,
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
            f"classify_total_s={classify_total_s:.1f}",
            f"judge_calls={judge_calls}",
            f"judge_total_s={judge_total_s:.1f}",
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
    prefilter_dropped: int = 0
    classifier_dropped: int = 0
    written: int = 0
    green: int = 0
    amber: int = 0
    red: int = 0
    enrich_failed: int = 0
    external_redirects: int = 0
    errored: int = 0
    parsers_dead: int = 0
    duration_seconds: float = 0.0


def run(
    config_path: Path,
    *,
    extractor: LLMExtractor | None = None,
    prefilter: DomainPreFilter | None = None,
    parser_registry: Callable[[str], type[Parser] | None] | None = None,
    dedup_store: DeduplicationStore | None = None,
    results_manager: ResultsFileManager | None = None,
    layout: Layout | None = None,
) -> RunSummary:
    _start = time.monotonic()

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
        extractor = OllamaExtractor(cfg, prompts)

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
    _resolve = parser_registry if parser_registry is not None else _default_registry.get
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
    prefilter_dropped = 0
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

        parser_inbound: dict[str, queue.Queue[object]] = {}
        parser_thresholds: dict[str, int] = {}
        threads: list[tuple[str, _ParserThread]] = []

        for parser, source in parsers_list:
            parser_id = source.parser_type
            inbound: queue.Queue[object] = queue.Queue()
            parser_inbound[parser_id] = inbound
            parser_thresholds[parser_id] = getattr(
                parser, "page_size", _DISCOVER_SHORT_CIRCUIT_FALLBACK
            )
            worklist = [
                ParserQuery(keyword=kw, location=loc, max_results=source.max_results)
                for kw in cfg.keywords
                for loc in locations
            ]
            t = _ParserThread(parser_id, parser, worklist, outbound, inbound)
            threads.append((parser_id, t))

        parser_starts: dict[str, tuple[datetime, float]] = {}
        for pid, t in threads:
            t.start()
            parser_starts[pid] = (datetime.now(timezone.utc), time.monotonic())
            _log.info("parser %s started", pid)
            parser_log.record(pid, "parser started")

        parsers_remaining: set[str] = set(parser_inbound.keys())
        consecutive_url_hits: dict[str, int] = {pid: 0 for pid in parsers_remaining}
        _pending_enrich: dict[str, PositionStub] = {}
        discovered_per_parser: dict[str, int] = {}

        while parsers_remaining:
            pid, payload = outbound.get()
            current_stage.set(f"parser:{pid}")

            if isinstance(payload, PositionStub):
                discovered += 1
                discovered_per_parser[pid] = discovered_per_parser.get(pid, 0) + 1
                seen_result = dedup_store.is_seen(payload)
                threshold = parser_thresholds[pid]

                if seen_result == "miss":
                    consecutive_url_hits[pid] = 0
                    _pending_enrich[pid] = payload
                    parser_inbound[pid].put(_ENRICH)
                elif seen_result == "url_hit":
                    consecutive_url_hits[pid] += 1
                    skipped += 1
                    if consecutive_url_hits[pid] >= threshold:
                        consecutive_url_hits[pid] = 0
                        parser_inbound[pid].put(_SKIP_AND_END_QUERY)
                    else:
                        parser_inbound[pid].put(_SKIP)
                else:  # tuple_hit
                    consecutive_url_hits[pid] = 0
                    skipped += 1
                    parser_inbound[pid].put(_SKIP)

            elif isinstance(payload, Position):
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
                if verdict.passes:
                    survivors.append((payload, resolution))
                else:
                    dedup_store.mark_seen(payload.stub, "off_domain")
                    prefilter_dropped += 1

            elif isinstance(payload, ParserError):
                stub = _pending_enrich.pop(pid, None)
                if stub is not None:
                    _log.warning(
                        "enrich failed: parser_id=%s stub_url=%s message=%s",
                        pid,
                        stub.url,
                        payload,
                    )
                    dedup_store.mark_seen(stub, "enrich_failed")
                enrich_failed += 1

            elif isinstance(payload, ExternalRedirect):
                stub = _pending_enrich.pop(pid, None)
                if stub is not None:
                    _log.info(
                        "external_redirect parser_id=%s stub_url=%s outbound=%s",
                        pid,
                        stub.url,
                        payload.outbound_url,
                    )
                    dedup_store.mark_seen(stub, "external_redirect")
                external_redirects += 1

            elif payload is _PARSER_DONE:
                parsers_remaining.discard(pid)

            elif isinstance(payload, _ParserDead):
                _log.error(
                    "parser thread died: parser_id=%s\n%s",
                    pid,
                    payload.traceback_str,
                )
                parsers_dead += 1
                parsers_remaining.discard(pid)

        for _, t in threads:
            t.join()

        parsers_done_monotonic = time.monotonic()
        for pid, (started_at, started_monotonic) in parser_starts.items():
            parser_log.summarize(
                pid,
                {
                    "discovered": discovered_per_parser.get(pid, 0),
                    "duration": round(parsers_done_monotonic - started_monotonic, 1),
                },
                started_at,
            )
        parser_log.summarize(
            "language",
            {"anomalies": language_anomalies},
            _run_started_at,
        )

    # Step 10: Classify batch — all classify_relevance calls before any judge_match call
    current_stage.set("classify")
    classifier_dropped = 0
    errored = 0
    classify_calls = 0
    classify_total_s = 0.0
    in_domain: list[tuple[Position, LanguageResolution]] = []
    for position, resolution in survivors:
        try:
            _t0 = time.monotonic()
            rel_verdict = extractor.classify_relevance(
                resolution.effective, position.title, position.raw_description
            )
            classify_total_s += time.monotonic() - _t0
            classify_calls += 1
        except ExtractorError as exc:
            _log.warning("classify_relevance failed: %s", exc)
            errored += 1
            continue
        if rel_verdict.in_domain:
            in_domain.append((position, resolution))
        else:
            dedup_store.mark_seen(position.stub, "off_domain")
            classifier_dropped += 1

    # Step 11: Judge batch — all judge_match calls after classify batch
    current_stage.set("judge")
    judged: list[tuple[Position, MatchVerdict]] = []
    judge_calls = 0
    judge_total_s = 0.0
    for position, resolution in in_domain:
        try:
            _t0 = time.monotonic()
            match_verdict = extractor.judge_match(
                resolution.effective, position.raw_description
            )
            judge_total_s += time.monotonic() - _t0
            judge_calls += 1
        except ExtractorError as exc:
            _log.warning("judge_match failed: %s", exc)
            errored += 1
            continue
        judged.append((position, match_verdict))

    # Step 12: Render → append+fsync → mark_seen("kept"), strictly in order
    current_stage.set("results_write")
    written = 0
    green = 0
    amber = 0
    red = 0
    written_per_source: dict[str, int] = {}
    for position, match_verdict in judged:
        number = results_manager.next_position_number()
        rendered = render(position, match_verdict, number, layout)
        results_manager.append(rendered)
        dedup_store.mark_seen(position.stub, "kept")
        written += 1
        src = position.stub.source
        written_per_source[src] = written_per_source.get(src, 0) + 1
        if match_verdict.tier == MatchTier.green:
            green += 1
        elif match_verdict.tier == MatchTier.amber:
            amber += 1
        else:
            red += 1

    # Step 13: Append Run Divider — only on successful completion
    elapsed_s = time.monotonic() - _start
    divider = _format_run_divider(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        tag=_discover_release_tag(),
        sources=written_per_source,
        kept=written,
        errors=errored,
        classify_calls=classify_calls,
        classify_total_s=classify_total_s,
        judge_calls=judge_calls,
        judge_total_s=judge_total_s,
        elapsed_s=elapsed_s,
    )
    try:
        results_manager.append(divider)
    except ResultsFileError as exc:
        _log.error("run divider append failed: %s", exc)
        raise

    _log.info(
        "run complete: discovered=%d skipped=%d prefilter_dropped=%d "
        "classifier_dropped=%d written=%d green=%d amber=%d red=%d "
        "enrich_failed=%d external_redirects=%d errored=%d parsers_dead=%d",
        discovered,
        skipped,
        prefilter_dropped,
        classifier_dropped,
        written,
        green,
        amber,
        red,
        enrich_failed,
        external_redirects,
        errored,
        parsers_dead,
    )

    return RunSummary(
        duration_seconds=elapsed_s,
        discovered=discovered,
        skipped=skipped,
        prefilter_dropped=prefilter_dropped,
        classifier_dropped=classifier_dropped,
        written=written,
        green=green,
        amber=amber,
        red=red,
        enrich_failed=enrich_failed,
        external_redirects=external_redirects,
        errored=errored,
        parsers_dead=parsers_dead,
    )
