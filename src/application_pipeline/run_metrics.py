from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from application_pipeline.content_gate import ContentSnapshot
from application_pipeline.dedup_counters import DedupSnapshot
from application_pipeline.freshness_gate import FreshnessSnapshot
from application_pipeline.llm.types import CallUsage
from application_pipeline.parser_log import RunLog
from application_pipeline.prefilter_gate import PreFilterSnapshot
from application_pipeline.status_display import StatusDisplay


@dataclass
class _ParserCounters:
    total_queries: int = 0
    discovered: int = 0
    enrich_failed: int = 0
    not_served_queries: int = 0
    parsers_dead: int = 0
    unparseable_dates: int = 0
    enriched: int = 0
    native_enriched: int = 0
    has_native_enrich: bool = False
    queries_done: int = 0
    freshness_dropped: int = 0
    dedup_dropped: int = 0
    prefilter_dropped: int = 0
    enrich_failed_count: int = 0
    content_dropped: int = 0
    forwarded: int = 0
    order: int = 0
    gates_registered: bool = False


@dataclass(frozen=True)
class RunSummary:
    discovered: int = 0
    skipped: int = 0
    prefilter_considered: int = 0
    prefilter_passed: int = 0
    prefilter_dropped: int = 0
    prefilter_blacklist_hits: int = 0
    content_considered: int = 0
    content_passed: int = 0
    content_dropped_empty_body: int = 0
    content_dropped_too_short: int = 0
    dedup_url_hits: int = 0
    dedup_tuple_hits: int = 0
    dedup_run_hits: int = 0
    dedup_misses: int = 0
    judge_resumed: int = 0
    classifier_dropped: int = 0
    written: int = 0
    enrich_failed: int = 0
    errored: int = 0
    parsers_dead: int = 0
    classify_items: int = 0
    claude_input_tokens: int = 0
    claude_output_tokens: int = 0
    claude_cache_read_tokens: int = 0
    claude_cost_usd: float = 0.0
    duration_seconds: float = 0.0


class RunMetrics:
    """Owns all run-level counters and produces Run Divider / RunSummary output."""

    def __init__(self, display: StatusDisplay, *, run_log: RunLog) -> None:
        self._display = display
        self._run_log = run_log
        self._lock = threading.Lock()
        self._classify_lock = threading.Lock()

        # Parser-side (main) counters
        self._discovered = 0
        self._enrich_failed = 0
        self._parsers_dead = 0

        # Classify-stage counters
        self._classify_calls = 0
        self._classify_items = 0
        self._classify_failed = 0
        self._classify_total_s = 0.0
        self._classify_input_tokens = 0
        self._classify_output_tokens = 0
        self._classify_cache_read_tokens = 0
        self._classify_cost_usd = 0.0
        self._classifier_dropped = 0
        self._classify_items_errored = 0
        self._classify_items_retryable = 0
        self._total_batches = 0
        self._classify_queued = 0
        self._classifying = 0

        # Pending-depth counters
        self._pending_classify = 0
        self._pending_judge = 0

        # Judge-stage counters
        self._judge_started = 0
        self._judge_calls = 0
        self._judge_failed = 0
        self._judge_total_s = 0.0
        self._judge_input_tokens = 0
        self._judge_output_tokens = 0
        self._judge_cache_read_tokens = 0
        self._judge_cost_usd = 0.0
        self._written = 0
        self._written_per_source: dict[str, int] = {}
        self._judge_errored = 0

        self._degraded_reason: str | None = None

        # Per-parser counters (lazily allocated on first event)
        self._per_parser: dict[str, _ParserCounters] = {}

    # -----------------------------------------------------------------------
    # Row registration
    # -----------------------------------------------------------------------

    @staticmethod
    def _parser_row(parser_id: str) -> str:
        return "parser " + parser_id.replace("_", " ")

    @staticmethod
    def _gates_row(parser_id: str) -> str:
        return "parser " + parser_id.replace("_", " ") + " gates"

    def register_rows(self) -> None:
        self._display.register("llm classify relevance", order=1001, phase="running")

    def register_parser(
        self,
        parser_id: str,
        *,
        order: int,
        total_queries: int,
        has_native_enrich: bool = False,
    ) -> None:
        with self._lock:
            entry = self._parser_entry(parser_id)
            entry.total_queries = total_queries
            entry.has_native_enrich = has_native_enrich
            entry.order = order
            body = self._parser_body(parser_id)
        self._display.register(
            self._parser_row(parser_id),
            order=order,
            phase="running",
            body=body,
        )

    def _register_gates_row(self, parser_id: str, order: int, gates_body: str) -> None:
        self._display.register(
            self._gates_row(parser_id),
            order=order + 1,
            phase="running",
            body=gates_body,
        )

    # -----------------------------------------------------------------------
    # Degraded reason
    # -----------------------------------------------------------------------

    def set_degraded_reason(self, reason: str) -> None:
        with self._lock:
            self._degraded_reason = reason

    # -----------------------------------------------------------------------
    # Internal per-parser helpers (called under lock)
    # -----------------------------------------------------------------------

    def _parser_entry(self, parser_id: str) -> _ParserCounters:
        try:
            return self._per_parser[parser_id]
        except KeyError:
            entry = _ParserCounters()
            self._per_parser[parser_id] = entry
            return entry

    # -----------------------------------------------------------------------
    # Parser-side events
    # -----------------------------------------------------------------------

    def discovered(self, parser_id: str = "") -> None:
        with self._lock:
            self._discovered += 1
            if parser_id:
                self._parser_entry(parser_id).discovered += 1
            pipeline_body = self._pipeline_body()
            parser_body = self._parser_body(parser_id) if parser_id else None
        self._display.update_body("pipeline", body=pipeline_body)
        if parser_id and parser_body is not None:
            self._display.update_body(self._parser_row(parser_id), body=parser_body)

    def enrich_failed(self, parser_id: str = "") -> None:
        with self._lock:
            self._enrich_failed += 1
            if parser_id:
                self._parser_entry(parser_id).enrich_failed += 1
            pipeline_body = self._pipeline_body()
            parser_body = self._parser_body(parser_id) if parser_id else None
        self._display.update_body("pipeline", body=pipeline_body)
        if parser_id and parser_body is not None:
            self._display.update_body(self._parser_row(parser_id), body=parser_body)

    def parser_dead(self, parser_id: str = "") -> None:
        with self._lock:
            self._parsers_dead += 1
            if parser_id:
                entry = self._parser_entry(parser_id)
                entry.parsers_dead += 1
                gates_registered = entry.gates_registered
            else:
                gates_registered = False
            pipeline_body = self._pipeline_body()
            parser_body = self._parser_body(parser_id) if parser_id else None
        self._display.update_body("pipeline", body=pipeline_body)
        if parser_id and parser_body is not None:
            self._display.update_body(self._parser_row(parser_id), body=parser_body)
            self._display.update_phase(self._parser_row(parser_id), phase="dead")
            if gates_registered:
                self._display.update_phase(self._gates_row(parser_id), phase="dead")

    def parser_done(self, parser_id: str) -> None:
        with self._lock:
            entry = self._parser_entry(parser_id)
            body = self._parser_body(parser_id)
            gates_registered = entry.gates_registered
        self._display.update_body(self._parser_row(parser_id), body=body)
        self._display.update_phase(self._parser_row(parser_id), phase="done")
        if gates_registered:
            self._display.update_phase(self._gates_row(parser_id), phase="done")

    def not_served_query(self, parser_id: str) -> None:
        with self._lock:
            self._parser_entry(parser_id).not_served_queries += 1
            body = self._parser_body(parser_id)
        self._display.update_body(self._parser_row(parser_id), body=body)

    def unparseable_date(self, parser_id: str) -> None:
        with self._lock:
            self._parser_entry(parser_id).unparseable_dates += 1
            body = self._parser_body(parser_id)
        self._display.update_body(self._parser_row(parser_id), body=body)

    def query_done(self, parser_id: str) -> None:
        with self._lock:
            self._parser_entry(parser_id).queries_done += 1
            body = self._parser_body(parser_id)
        self._display.update_body(self._parser_row(parser_id), body=body)

    def enriched(self, parser_id: str, mode: str) -> None:
        with self._lock:
            entry = self._parser_entry(parser_id)
            entry.enriched += 1
            if mode == "native":
                entry.native_enriched += 1
            body = self._parser_body(parser_id)
        self._display.update_body(self._parser_row(parser_id), body=body)

    def _apply_gate_drop(self, parser_id: str, field: str) -> None:
        with self._lock:
            entry = self._parser_entry(parser_id)
            setattr(entry, field, getattr(entry, field) + 1)
            first_drop = not entry.gates_registered
            if first_drop:
                entry.gates_registered = True
            order = entry.order
            gates_body = self._gates_body(parser_id)
        if first_drop:
            self._register_gates_row(parser_id, order, gates_body)
        else:
            self._display.update_body(self._gates_row(parser_id), body=gates_body)

    def increment_freshness_dropped(self, parser_id: str) -> None:
        self._apply_gate_drop(parser_id, "freshness_dropped")

    def increment_dedup_dropped(self, parser_id: str) -> None:
        self._apply_gate_drop(parser_id, "dedup_dropped")

    def increment_prefilter_dropped(self, parser_id: str) -> None:
        self._apply_gate_drop(parser_id, "prefilter_dropped")

    def increment_enrich_failed_count(self, parser_id: str) -> None:
        with self._lock:
            self._parser_entry(parser_id).enrich_failed_count += 1
            body = self._parser_body(parser_id)
        self._display.update_body(self._parser_row(parser_id), body=body)

    def increment_content_dropped(self, parser_id: str) -> None:
        self._apply_gate_drop(parser_id, "content_dropped")

    def observe_parser_drop(
        self,
        parser_id: str,
        *,
        outcome: Literal[
            "freshness_discover",
            "freshness_post_enrich",
            "dedup_url_hit",
            "dedup_tuple_hit",
            "dedup_fuzzy_hit",
            "dedup_run_hit",
            "prefilter",
            "content_empty_body",
            "content_too_short",
        ],
    ) -> None:
        if outcome in ("freshness_discover", "freshness_post_enrich"):
            self.increment_freshness_dropped(parser_id)
            return
        if outcome in (
            "dedup_url_hit",
            "dedup_tuple_hit",
            "dedup_fuzzy_hit",
            "dedup_run_hit",
        ):
            self.increment_dedup_dropped(parser_id)
            return
        if outcome == "prefilter":
            self.increment_prefilter_dropped(parser_id)
            return
        self.increment_content_dropped(parser_id)

    def increment_forwarded(self, parser_id: str) -> None:
        with self._lock:
            self._parser_entry(parser_id).forwarded += 1
            body = self._parser_body(parser_id)
        self._display.update_body(self._parser_row(parser_id), body=body)

    def parser_summary(
        self, parser_id: str, end_monotonic: float, started_monotonic: float
    ) -> dict[str, int | float]:
        with self._lock:
            c = self._per_parser.get(parser_id, _ParserCounters())
            return {
                "discovered": c.discovered,
                "enrich_failed": c.enrich_failed,
                "not_served_queries": c.not_served_queries,
                "parsers_dead": c.parsers_dead,
                "unparseable_dates": c.unparseable_dates,
                "duration": round(end_monotonic - started_monotonic, 1),
            }

    # -----------------------------------------------------------------------
    # Classify-stage events
    # -----------------------------------------------------------------------

    def classify_buffered(self, n: int) -> None:
        with self._classify_lock:
            self._pending_classify += n
            self._classify_queued += n
            body = self._classify_body()
        self._display.update_body("llm classify relevance", body=body)

    def classify_batch_enqueued(self, n: int) -> None:
        with self._classify_lock:
            body = self._classify_body()
        self._display.update_body("llm classify relevance", body=body)

    def classify_batch_dequeued(self, n: int) -> None:
        with self._classify_lock:
            self._total_batches += 1
            self._pending_classify -= n
            self._classifying += n
            body = self._classify_body()
        self._display.update_body("llm classify relevance", body=body)

    def classify_batch_complete(
        self,
        usage: CallUsage,
        items: int,
        classifier_dropped: int,
        retryable_items: int = 0,
    ) -> None:
        with self._classify_lock:
            self._classify_calls += 1
            self._classify_items += items
            self._classify_input_tokens += usage.input_tokens
            self._classify_output_tokens += usage.output_tokens
            self._classify_cache_read_tokens += usage.cache_read_tokens
            self._classify_cost_usd += usage.cost_usd
            self._classify_total_s += usage.duration_s
            self._classifier_dropped += classifier_dropped
            self._classify_items_retryable += retryable_items
            self._classifying -= items
            body = self._classify_body()
        self._display.update_body("llm classify relevance", body=body)

    def classify_batch_failed(self, items: int) -> None:
        with self._classify_lock:
            self._classify_failed += 1
            self._classify_items_errored += items
            self._classifying -= items
            body = self._classify_body()
        self._display.update_body("llm classify relevance", body=body)

    def classify_done(self) -> None:
        self._display.update_phase("llm classify relevance", phase="done")

    # -----------------------------------------------------------------------
    # Judge-stage events
    # -----------------------------------------------------------------------

    def judge_enqueued(self) -> None:
        with self._lock:
            self._pending_judge += 1

    def judge_dequeued(self) -> None:
        with self._lock:
            self._pending_judge -= 1
            self._judge_started += 1

    def judge_complete(self, usage: CallUsage, source: str) -> None:
        with self._lock:
            self._judge_calls += 1
            self._judge_input_tokens += usage.input_tokens
            self._judge_output_tokens += usage.output_tokens
            self._judge_cache_read_tokens += usage.cache_read_tokens
            self._judge_cost_usd += usage.cost_usd
            self._judge_total_s += usage.duration_s
            self._written += 1
            self._written_per_source[source] = (
                self._written_per_source.get(source, 0) + 1
            )

    def judge_failed(self) -> None:
        with self._lock:
            self._judge_failed += 1
            self._judge_errored += 1
            pipeline_body = self._pipeline_body()
        self._display.update_body("pipeline", body=pipeline_body)

    _JUDGE_ROW = "llm judge match"
    _JUDGE_ROW_ORDER = 1002

    def judge_started(self, candidate_count: int) -> None:
        """Lazily register the judge row when the judge step is about to begin."""
        self._display.register(
            self._JUDGE_ROW,
            order=self._JUDGE_ROW_ORDER,
            phase="running",
            body=f"{candidate_count} candidates",
        )

    def judge_top_n_complete(self, usage: CallUsage, card_count: int) -> None:
        with self._lock:
            self._judge_calls += 1
            self._judge_started += 1
            self._judge_input_tokens += usage.input_tokens
            self._judge_output_tokens += usage.output_tokens
            self._judge_cache_read_tokens += usage.cache_read_tokens
            self._judge_cost_usd += usage.cost_usd
            self._judge_total_s += usage.duration_s
            self._written += card_count
        self._display.update_body(self._JUDGE_ROW, body=f"wrote {card_count} cards")
        self._display.update_phase(self._JUDGE_ROW, phase="done")
        self._display.print(
            caller="llm_judge_match",
            message=f"judge_top_n complete: wrote {card_count} cards",
        )

    def judge_top_n_failed(self) -> None:
        """Transition the judge row out of 'running' when judge_top_n fails."""
        self._display.update_phase(self._JUDGE_ROW, phase="error")

    # -----------------------------------------------------------------------
    # Read-only accessors for run_complete event
    # -----------------------------------------------------------------------

    @property
    def classify_calls(self) -> int:
        with self._classify_lock:
            return self._classify_calls

    @property
    def classify_input_tokens(self) -> int:
        with self._classify_lock:
            return self._classify_input_tokens

    @property
    def classify_output_tokens(self) -> int:
        with self._classify_lock:
            return self._classify_output_tokens

    @property
    def judge_input_tokens(self) -> int:
        with self._lock:
            return self._judge_input_tokens

    @property
    def judge_output_tokens(self) -> int:
        with self._lock:
            return self._judge_output_tokens

    # -----------------------------------------------------------------------
    # Output methods
    # -----------------------------------------------------------------------

    def format_run_divider(
        self, timestamp: str, tag: str | None, elapsed_s: float, *, dedup: DedupSnapshot
    ) -> str:
        with self._classify_lock:
            classify_calls = self._classify_calls
            classify_items = self._classify_items
            classify_total_s = self._classify_total_s
            classify_input_tokens = self._classify_input_tokens
            classify_output_tokens = self._classify_output_tokens
            classify_cache_read_tokens = self._classify_cache_read_tokens
            classify_cost_usd = self._classify_cost_usd
            classify_batches_failed = self._classify_failed
            classify_items_abandoned = self._classify_items_errored

        with self._lock:
            sources = dict(self._written_per_source)
            kept = self._written
            judge_calls = self._judge_calls
            judge_total_s = self._judge_total_s
            judge_input_tokens = self._judge_input_tokens
            judge_output_tokens = self._judge_output_tokens
            judge_cache_read_tokens = self._judge_cache_read_tokens
            judge_cost_usd = self._judge_cost_usd
            degraded_reason = self._degraded_reason
            # Roll up classify-stage abandons into the judge-stage error total,
            # matching today's `judge_stats.errored += classify_stats.items_errored`
            # step that runs before the divider is formatted.
            judge_items_abandoned = self._judge_errored + classify_items_abandoned
            errors = judge_items_abandoned

        dedup_url_hits = dedup.dedup_url_hits
        dedup_tuple_hits = dedup.dedup_tuple_hits
        dedup_run_hits = dedup.dedup_run_hits
        dedup_misses = dedup.dedup_misses
        judge_resumed = dedup.judge_resumed

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
                f"dedup_url_hits={dedup_url_hits}",
                f"dedup_tuple_hits={dedup_tuple_hits}",
                f"dedup_run_hits={dedup_run_hits}",
                f"dedup_misses={dedup_misses}",
                f"classify_calls={classify_calls}",
                f"classify_items={classify_items}",
                f"classify_total_s={classify_total_s:.1f}",
                f"judge_calls={judge_calls}",
                f"judge_total_s={judge_total_s:.1f}",
                f"classify_input_tokens={classify_input_tokens}",
                f"classify_output_tokens={classify_output_tokens}",
                f"classify_cache_read_tokens={classify_cache_read_tokens}",
                f"classify_cost_usd={classify_cost_usd:.6f}",
                f"judge_input_tokens={judge_input_tokens}",
                f"judge_output_tokens={judge_output_tokens}",
                f"judge_cache_read_tokens={judge_cache_read_tokens}",
                f"judge_cost_usd={judge_cost_usd:.6f}",
                f"elapsed_s={elapsed_s:.1f}",
            ]
        )
        if judge_resumed > 0:
            parts.append(f"judge_resumed={judge_resumed}")
        if degraded_reason is not None:
            parts.append(f"degraded_reason={degraded_reason}")
        if classify_batches_failed > 0:
            parts.append(f"classify_batches_failed={classify_batches_failed}")
        if classify_items_abandoned > 0:
            parts.append(f"classify_items_abandoned={classify_items_abandoned}")
        if judge_items_abandoned > 0:
            parts.append(f"judge_items_abandoned={judge_items_abandoned}")
        return f"<!-- {' '.join(parts)} -->\n"

    def to_run_summary(
        self,
        duration_s: float,
        prefilter: PreFilterSnapshot,
        freshness: FreshnessSnapshot,
        content: ContentSnapshot,
        dedup: DedupSnapshot,
    ) -> RunSummary:
        with self._classify_lock:
            classify_input_tokens = self._classify_input_tokens
            classify_output_tokens = self._classify_output_tokens
            classify_cache_read_tokens = self._classify_cache_read_tokens
            classify_cost_usd = self._classify_cost_usd
            classify_items = self._classify_items
            classifier_dropped = self._classifier_dropped
            classify_items_errored = self._classify_items_errored

        with self._lock:
            return RunSummary(
                duration_seconds=duration_s,
                discovered=self._discovered,
                skipped=dedup.skipped,
                prefilter_considered=prefilter.prefilter_considered,
                prefilter_passed=prefilter.prefilter_passed,
                prefilter_dropped=prefilter.prefilter_dropped,
                prefilter_blacklist_hits=prefilter.prefilter_blacklist_hits,
                content_considered=content.content_considered,
                content_passed=content.content_passed,
                content_dropped_empty_body=content.content_dropped_empty_body,
                content_dropped_too_short=content.content_dropped_too_short,
                dedup_url_hits=dedup.dedup_url_hits,
                dedup_tuple_hits=dedup.dedup_tuple_hits,
                dedup_run_hits=dedup.dedup_run_hits,
                dedup_misses=dedup.dedup_misses,
                judge_resumed=dedup.judge_resumed,
                classifier_dropped=classifier_dropped,
                written=self._written,
                enrich_failed=self._enrich_failed,
                errored=self._judge_errored + classify_items_errored,
                parsers_dead=self._parsers_dead,
                classify_items=classify_items,
                claude_input_tokens=classify_input_tokens + self._judge_input_tokens,
                claude_output_tokens=classify_output_tokens + self._judge_output_tokens,
                claude_cache_read_tokens=classify_cache_read_tokens
                + self._judge_cache_read_tokens,
                claude_cost_usd=classify_cost_usd + self._judge_cost_usd,
            )

    def summarize_to_parser_log(self, started_at: datetime) -> None:
        with self._classify_lock:
            classify_calls = self._classify_calls
            classify_items = self._classify_items
            classifier_dropped = self._classifier_dropped
            classify_failed = self._classify_failed
            classify_input_tokens = self._classify_input_tokens
            classify_output_tokens = self._classify_output_tokens
            classify_cache_read_tokens = self._classify_cache_read_tokens
            classify_cost_usd = self._classify_cost_usd
            classify_total_s = self._classify_total_s

        with self._lock:
            judge_calls = self._judge_calls
            judge_failed = self._judge_failed
            judge_input_tokens = self._judge_input_tokens
            judge_output_tokens = self._judge_output_tokens
            judge_cache_read_tokens = self._judge_cache_read_tokens
            judge_cost_usd = self._judge_cost_usd
            judge_total_s = self._judge_total_s

        self._run_log.summary(
            "llm_classify_relevance",
            {
                "batches_sent": classify_calls,
                "items_classified": classify_items,
                "matched": classify_items - classifier_dropped,
                "off_domain": classifier_dropped,
                "batches_failed": classify_failed,
                "input_tokens": classify_input_tokens,
                "output_tokens": classify_output_tokens,
                "cache_read_tokens": classify_cache_read_tokens,
                "cost_usd": round(classify_cost_usd, 6),
                "duration_s": round(classify_total_s, 1),
            },
            started_at,
        )
        self._run_log.summary(
            "llm_judge_match",
            {
                "judges_sent": judge_calls,
                "judges_failed": judge_failed,
                "input_tokens": judge_input_tokens,
                "output_tokens": judge_output_tokens,
                "cache_read_tokens": judge_cache_read_tokens,
                "cost_usd": round(judge_cost_usd, 6),
                "duration_s": round(judge_total_s, 1),
            },
            started_at,
        )

    # -----------------------------------------------------------------------
    # Internal body formatters (called under lock)
    # -----------------------------------------------------------------------

    def _parser_body(self, parser_id: str) -> str:
        c = self._per_parser.get(parser_id, _ParserCounters())
        parts = [f"{c.discovered} discovered"]
        if c.has_native_enrich and c.enrich_failed_count:
            parts.append(f"{c.enrich_failed_count} enrich_failed")
        parts.append(f"{c.forwarded} forwarded")
        return " · ".join(parts)

    def _gates_body(self, parser_id: str) -> str:
        c = self._per_parser.get(parser_id, _ParserCounters())
        parts = []
        if c.freshness_dropped:
            parts.append(f"{c.freshness_dropped} freshness")
        if c.dedup_dropped:
            parts.append(f"{c.dedup_dropped} dedup")
        if c.prefilter_dropped:
            parts.append(f"{c.prefilter_dropped} pre-filter")
        if c.content_dropped:
            parts.append(f"{c.content_dropped} content")
        return " · ".join(parts)

    def _pipeline_body(self) -> str:
        return (
            f"discovered={self._discovered} written={self._written}"
            f" errors={self._enrich_failed + self._parsers_dead + self._judge_errored}"
        )

    def _classify_body(self) -> str:
        depth = self._pending_classify
        malformed = self._classify_items_errored + self._classify_items_retryable
        forwarded = (
            self._classify_items
            - self._classifier_dropped
            - self._classify_items_retryable
        )
        parts = []
        if depth > 0:
            parts.append(f"{depth} queued")
        if self._classifying > 0:
            parts.append(f"{self._classifying} classifying")
        if malformed > 0:
            parts.append(f"{malformed} malformed")
        if self._classifier_dropped > 0:
            parts.append(f"{self._classifier_dropped} dropped")
        if forwarded > 0:
            parts.append(f"{forwarded} forwarded")
        return " · ".join(parts)
