from __future__ import annotations

import contextvars
import logging
import queue
import threading
import time
import traceback
from collections import defaultdict
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from application_pipeline import config as config_module
from application_pipeline import dedup as dedup_module
from application_pipeline import layout as layout_module
from application_pipeline import parser_log
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
class _BatchStats:
    classify_calls: int = 0
    classify_items: int = 0
    classify_failed: int = 0
    classify_total_s: float = 0.0
    classify_input_tokens: int = 0
    classify_output_tokens: int = 0
    classify_cache_read_tokens: int = 0
    classify_cost_usd: float = 0.0
    judge_calls: int = 0
    judge_failed: int = 0
    judge_total_s: float = 0.0
    judge_input_tokens: int = 0
    judge_output_tokens: int = 0
    judge_cache_read_tokens: int = 0
    judge_cost_usd: float = 0.0
    classifier_dropped: int = 0
    errored: int = 0
    written: int = 0
    green: int = 0
    amber: int = 0
    red: int = 0
    written_per_source: dict[str, int] = field(default_factory=dict)


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


def _process_batch(
    batch: list[tuple[Position, LanguageResolution]],
    language: str,
    extractor: LLMExtractor,
    dedup_store: DeduplicationStore,
    results_manager: ResultsFileManager,
    layout: Layout,
    stats: _BatchStats,
) -> None:
    """Classify a batch, then pipeline judge → write for each in-domain survivor."""
    items = _make_classify_items(batch)
    try:
        _t0 = time.monotonic()
        verdicts, classify_usage = extractor.classify_relevance_batch(language, items)
        stats.classify_total_s += time.monotonic() - _t0
        stats.classify_calls += 1
        stats.classify_items += len(items)
        stats.classify_input_tokens += classify_usage.input_tokens
        stats.classify_output_tokens += classify_usage.output_tokens
        stats.classify_cache_read_tokens += classify_usage.cache_read_tokens
        stats.classify_cost_usd += classify_usage.cost_usd
    except ClaudeUsageLimitError:
        raise
    except ExtractorError as exc:
        _log.warning("classify_relevance_batch failed: %s", exc)
        parser_log.record(
            "classify_relevance",
            "batch_error",
            language=language,
            batch_size=len(batch),
            error=str(exc),
        )
        stats.classify_failed += 1
        stats.errored += len(batch)
        return

    for verdict, (position, resolution) in zip(verdicts, batch):
        if not verdict.in_domain:
            dedup_store.mark_seen(position.stub, "off_domain")
            stats.classifier_dropped += 1
            continue

        try:
            _t0 = time.monotonic()
            match_verdict, judge_usage = extractor.judge_match(
                resolution.effective, position.raw_description
            )
            stats.judge_total_s += time.monotonic() - _t0
            stats.judge_calls += 1
            stats.judge_input_tokens += judge_usage.input_tokens
            stats.judge_output_tokens += judge_usage.output_tokens
            stats.judge_cache_read_tokens += judge_usage.cache_read_tokens
            stats.judge_cost_usd += judge_usage.cost_usd
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
            stats.judge_failed += 1
            stats.errored += 1
            continue

        current_stage.set("results_write")
        number = results_manager.next_position_number()
        rendered = render(position, match_verdict, number, layout)
        results_manager.append(rendered)
        dedup_store.mark_seen(position.stub, "kept")
        stats.written += 1
        src = position.stub.source
        stats.written_per_source[src] = stats.written_per_source.get(src, 0) + 1
        if match_verdict.tier == MatchTier.green:
            stats.green += 1
        elif match_verdict.tier == MatchTier.amber:
            stats.amber += 1
        else:
            stats.red += 1


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
) -> RunSummary:
    if status_display is None:
        status_display = PlainStatusDisplay()

    _start = time.monotonic()
    status_display.register("pipeline", order=0, phase="running")
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
                    ParserQuery(
                        keyword=kw, location=loc, max_results=source.max_results
                    )
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
            discovered_per_parser: dict[str, int] = defaultdict(int)
            enrich_failed_per_parser: dict[str, int] = defaultdict(int)
            external_redirects_per_parser: dict[str, int] = defaultdict(int)
            not_served_per_parser: dict[str, int] = defaultdict(int)
            parsers_dead_per_parser: dict[str, int] = defaultdict(int)
            unparseable_dates_per_parser: dict[str, int] = defaultdict(int)

            while parsers_remaining:
                pid, payload = outbound.get()
                current_stage.set(f"parser:{pid}")

                if isinstance(payload, PositionStub):
                    discovered += 1
                    discovered_per_parser[pid] += 1
                    seen_result = dedup_store.is_seen(payload)
                    threshold = parser_thresholds[pid]

                    if seen_result == "miss":
                        dedup_misses += 1
                        consecutive_url_hits[pid] = 0
                        _pending_enrich[pid] = payload
                        parser_inbound[pid].put(_ENRICH)
                    elif seen_result == "url_hit":
                        dedup_url_hits += 1
                        consecutive_url_hits[pid] += 1
                        skipped += 1
                        if consecutive_url_hits[pid] >= threshold:
                            consecutive_url_hits[pid] = 0
                            parser_inbound[pid].put(_SKIP_AND_END_QUERY)
                        else:
                            parser_inbound[pid].put(_SKIP)
                    else:  # tuple_hit
                        dedup_tuple_hits += 1
                        consecutive_url_hits[pid] = 0
                        skipped += 1
                        parser_inbound[pid].put(_SKIP)
                    status_display.update_body(
                        "pipeline",
                        body=f"discovered={discovered} written=0 errors={enrich_failed + parsers_dead}",
                    )

                elif isinstance(payload, Position):
                    for warning in payload._warnings:
                        parser_log.record(pid, warning)
                        if warning.startswith("unparseable_date"):
                            unparseable_dates_per_parser[pid] += 1
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
                        dedup_store.mark_seen(payload.stub, "off_domain")
                        prefilter_dropped += 1

                elif isinstance(payload, ParserError):
                    stub = _pending_enrich.pop(pid, None)
                    if stub is not None:
                        parser_log.record(
                            pid,
                            "enrich_failed",
                            stub_url=stub.url,
                            title=stub.title,
                            reason=str(payload),
                        )
                        dedup_store.mark_seen(stub, "enrich_failed")
                    enrich_failed += 1
                    enrich_failed_per_parser[pid] += 1
                    status_display.update_body(
                        "pipeline",
                        body=f"discovered={discovered} written=0 errors={enrich_failed + parsers_dead}",
                    )

                elif isinstance(payload, ExternalRedirect):
                    stub = _pending_enrich.pop(pid, None)
                    if stub is not None:
                        parser_log.record(
                            pid,
                            "external_redirect",
                            stub_url=stub.url,
                            outbound=payload.outbound_url,
                        )
                        dedup_store.mark_seen(stub, "external_redirect")
                    external_redirects += 1
                    external_redirects_per_parser[pid] += 1

                elif isinstance(payload, NotServedQuery):
                    not_served_per_parser[pid] += 1

                elif payload is _PARSER_DONE:
                    parsers_remaining.discard(pid)

                elif isinstance(payload, _ParserDead):
                    parser_log.record_traceback(pid, payload.traceback_str)
                    parsers_dead += 1
                    parsers_dead_per_parser[pid] += 1
                    parsers_remaining.discard(pid)
                    status_display.update_body(
                        "pipeline",
                        body=f"discovered={discovered} written=0 errors={enrich_failed + parsers_dead}",
                    )

            for _, t in threads:
                t.join()

            parsers_done_monotonic = time.monotonic()
            for pid, (started_at, started_monotonic) in parser_starts.items():
                parser_log.summarize(
                    pid,
                    {
                        "discovered": discovered_per_parser[pid],
                        "enrich_failed": enrich_failed_per_parser[pid],
                        "external_redirects": external_redirects_per_parser[pid],
                        "not_served_queries": not_served_per_parser[pid],
                        "parsers_dead": parsers_dead_per_parser[pid],
                        "unparseable_dates": unparseable_dates_per_parser[pid],
                        "duration": round(
                            parsers_done_monotonic - started_monotonic, 1
                        ),
                    },
                    started_at,
                )
            parser_log.summarize(
                "language",
                {"anomalies": language_anomalies},
                _run_started_at,
            )

        # Steps 10-12: Batched classify → judge survivors → append+fsync+mark-seen
        # Per-language buffers: de → de; en/other/unknown → en
        current_stage.set("classify")
        de_buffer: list[tuple[Position, LanguageResolution]] = []
        en_buffer: list[tuple[Position, LanguageResolution]] = []
        for position, resolution in survivors:
            if resolution.effective == "de":
                de_buffer.append((position, resolution))
            else:
                en_buffer.append((position, resolution))

        batch_size = cfg.claude_classify_batch_size
        stats = _BatchStats()

        for lang_str, lang_buffer in [("de", de_buffer), ("en", en_buffer)]:
            for i in range(0, len(lang_buffer), batch_size):
                _process_batch(
                    lang_buffer[i : i + batch_size],
                    lang_str,
                    extractor,
                    dedup_store,
                    results_manager,
                    layout,
                    stats,
                )
                status_display.update_body(
                    "pipeline",
                    body=f"discovered={discovered} written={stats.written} errors={enrich_failed + parsers_dead + stats.errored}",
                )

        # Emit per-call-site SUMMARY OF SESSION trailers
        parser_log.summarize(
            "classify_relevance",
            {
                "batches_sent": stats.classify_calls,
                "items_classified": stats.classify_items,
                "in_domain": stats.classify_items - stats.classifier_dropped,
                "off_domain": stats.classifier_dropped,
                "batches_failed": stats.classify_failed,
                "input_tokens": stats.classify_input_tokens,
                "output_tokens": stats.classify_output_tokens,
                "cache_read_tokens": stats.classify_cache_read_tokens,
                "cost_usd": round(stats.classify_cost_usd, 6),
                "duration_s": round(stats.classify_total_s, 1),
            },
            _run_started_at,
        )
        parser_log.summarize(
            "judge_match",
            {
                "judges_sent": stats.judge_calls,
                "judges_failed": stats.judge_failed,
                "green": stats.green,
                "amber": stats.amber,
                "red": stats.red,
                "input_tokens": stats.judge_input_tokens,
                "output_tokens": stats.judge_output_tokens,
                "cache_read_tokens": stats.judge_cache_read_tokens,
                "cost_usd": round(stats.judge_cost_usd, 6),
                "duration_s": round(stats.judge_total_s, 1),
            },
            _run_started_at,
        )

        # Step 13: Append Run Divider — only on successful completion
        elapsed_s = time.monotonic() - _start
        claude_input_tokens = stats.classify_input_tokens + stats.judge_input_tokens
        claude_output_tokens = stats.classify_output_tokens + stats.judge_output_tokens
        claude_cache_read_tokens = (
            stats.classify_cache_read_tokens + stats.judge_cache_read_tokens
        )
        claude_cost_usd = stats.classify_cost_usd + stats.judge_cost_usd
        divider = _format_run_divider(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            tag=_discover_release_tag(),
            sources=stats.written_per_source,
            kept=stats.written,
            errors=stats.errored,
            classify_calls=stats.classify_calls,
            classify_items=stats.classify_items,
            classify_total_s=stats.classify_total_s,
            judge_calls=stats.judge_calls,
            judge_total_s=stats.judge_total_s,
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
            stats.classifier_dropped,
            stats.written,
            stats.green,
            stats.amber,
            stats.red,
            enrich_failed,
            external_redirects,
            stats.errored,
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
            classifier_dropped=stats.classifier_dropped,
            written=stats.written,
            green=stats.green,
            amber=stats.amber,
            red=stats.red,
            enrich_failed=enrich_failed,
            external_redirects=external_redirects,
            errored=stats.errored,
            parsers_dead=parsers_dead,
            classify_items=stats.classify_items,
            claude_input_tokens=claude_input_tokens,
            claude_output_tokens=claude_output_tokens,
            claude_cache_read_tokens=claude_cache_read_tokens,
            claude_cost_usd=claude_cost_usd,
        )
    finally:
        status_display.stop()
