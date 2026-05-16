from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

from application_pipeline import parser_log
from application_pipeline.llm.types import CallUsage, MatchTier
from application_pipeline.prefilter import PreFilterVerdict
from application_pipeline.status_display import StatusDisplay


@dataclass
class _ParserCounters:
    discovered: int = 0
    enrich_failed: int = 0
    external_redirects: int = 0
    not_served_queries: int = 0
    parsers_dead: int = 0
    unparseable_dates: int = 0
    enriched: int = 0
    queries_done: int = 0


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
    dedup_run_hits: int = 0
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


class RunMetrics:
    """Owns all run-level counters and produces Run Divider / RunSummary output."""

    def __init__(self, display: StatusDisplay) -> None:
        self._display = display
        self._lock = threading.Lock()

        # Parser-side (main) counters
        self._discovered = 0
        self._skipped = 0
        self._dedup_url_hits = 0
        self._dedup_tuple_hits = 0
        self._dedup_run_hits = 0
        self._dedup_misses = 0
        self._prefilter_considered = 0
        self._prefilter_passed = 0
        self._prefilter_dropped = 0
        self._prefilter_whitelist_hits = 0
        self._prefilter_blacklist_hits = 0
        self._prefilter_no_hit_either = 0
        self._enrich_failed = 0
        self._external_redirects = 0
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
        self._total_batches = 0

        # Pending-depth counters
        self._pending_classify = 0
        self._pending_judge = 0

        # Judge-stage counters
        self._judge_calls = 0
        self._judge_failed = 0
        self._judge_total_s = 0.0
        self._judge_input_tokens = 0
        self._judge_output_tokens = 0
        self._judge_cache_read_tokens = 0
        self._judge_cost_usd = 0.0
        self._written = 0
        self._green = 0
        self._amber = 0
        self._red = 0
        self._written_per_source: dict[str, int] = {}
        self._judge_errored = 0

        self._degraded_reason: str | None = None

        # Per-parser counters (lazily allocated on first event)
        self._per_parser: dict[str, _ParserCounters] = {}

    # -----------------------------------------------------------------------
    # Row registration
    # -----------------------------------------------------------------------

    def register_rows(self, starting_order: int) -> None:
        self._display.register("dedup", order=starting_order, phase="running")
        self._display.register("prefilter", order=starting_order + 1, phase="running")
        self._display.register(
            "classify_relevance", order=starting_order + 2, phase="running"
        )
        self._display.register("judge_match", order=starting_order + 3, phase="running")

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
            body = self._pipeline_body()
        self._display.update_body("pipeline", body=body)

    def dedup_url_hit(self) -> None:
        with self._lock:
            self._dedup_url_hits += 1
            self._skipped += 1
            body = self._dedup_body()
        self._display.update_body("dedup", body=body)

    def dedup_tuple_hit(self) -> None:
        with self._lock:
            self._dedup_tuple_hits += 1
            self._skipped += 1
            body = self._dedup_body()
        self._display.update_body("dedup", body=body)

    def dedup_run_hit(self) -> None:
        with self._lock:
            self._dedup_run_hits += 1
            self._skipped += 1
            body = self._dedup_body()
        self._display.update_body("dedup", body=body)

    def dedup_miss(self) -> None:
        with self._lock:
            self._dedup_misses += 1
            body = self._dedup_body()
        self._display.update_body("dedup", body=body)

    def prefilter_passed(self, verdict: PreFilterVerdict) -> None:
        with self._lock:
            self._prefilter_considered += 1
            self._prefilter_passed += 1
            self._tally_prefilter_verdict(verdict)
            body = self._prefilter_body()
        self._display.update_body("prefilter", body=body)

    def prefilter_dropped(self, verdict: PreFilterVerdict) -> None:
        with self._lock:
            self._prefilter_considered += 1
            self._prefilter_dropped += 1
            self._tally_prefilter_verdict(verdict)
            body = self._prefilter_body()
        self._display.update_body("prefilter", body=body)

    def enrich_failed(self, parser_id: str = "") -> None:
        with self._lock:
            self._enrich_failed += 1
            if parser_id:
                self._parser_entry(parser_id).enrich_failed += 1
            body = self._pipeline_body()
        self._display.update_body("pipeline", body=body)

    def external_redirect(self, parser_id: str = "") -> None:
        with self._lock:
            self._external_redirects += 1
            if parser_id:
                self._parser_entry(parser_id).external_redirects += 1

    def parser_dead(self, parser_id: str = "") -> None:
        with self._lock:
            self._parsers_dead += 1
            if parser_id:
                self._parser_entry(parser_id).parsers_dead += 1
            body = self._pipeline_body()
        self._display.update_body("pipeline", body=body)

    def not_served_query(self, parser_id: str) -> None:
        with self._lock:
            self._parser_entry(parser_id).not_served_queries += 1

    def unparseable_date(self, parser_id: str) -> None:
        with self._lock:
            self._parser_entry(parser_id).unparseable_dates += 1

    def query_done(self, parser_id: str) -> None:
        with self._lock:
            self._parser_entry(parser_id).queries_done += 1

    def enriched(self, parser_id: str) -> None:
        with self._lock:
            self._parser_entry(parser_id).enriched += 1

    # -----------------------------------------------------------------------
    # Per-parser query/display accessors
    # -----------------------------------------------------------------------

    def parser_discovered(self, parser_id: str) -> int:
        with self._lock:
            return self._per_parser.get(parser_id, _ParserCounters()).discovered

    def parser_enriched(self, parser_id: str) -> int:
        with self._lock:
            return self._per_parser.get(parser_id, _ParserCounters()).enriched

    def parser_queries_done(self, parser_id: str) -> int:
        with self._lock:
            return self._per_parser.get(parser_id, _ParserCounters()).queries_done

    def parser_summary(
        self, parser_id: str, end_monotonic: float, started_monotonic: float
    ) -> dict[str, int | float]:
        with self._lock:
            c = self._per_parser.get(parser_id, _ParserCounters())
            return {
                "discovered": c.discovered,
                "enrich_failed": c.enrich_failed,
                "external_redirects": c.external_redirects,
                "not_served_queries": c.not_served_queries,
                "parsers_dead": c.parsers_dead,
                "unparseable_dates": c.unparseable_dates,
                "duration": round(end_monotonic - started_monotonic, 1),
            }

    # -----------------------------------------------------------------------
    # Classify-stage events
    # -----------------------------------------------------------------------

    def classify_buffered(self, n: int) -> None:
        with self._lock:
            self._pending_classify += n
            body = self._classify_body()
        self._display.update_body("classify_relevance", body=body)

    def classify_batch_enqueued(self, n: int) -> None:
        with self._lock:
            self._total_batches += 1
            body = self._classify_body()
        self._display.update_body("classify_relevance", body=body)

    def classify_batch_dequeued(self, n: int) -> None:
        with self._lock:
            self._pending_classify -= n
            body = self._classify_body()
        self._display.update_body("classify_relevance", body=body)

    def classify_batch_complete(
        self, usage: CallUsage, items: int, classifier_dropped: int
    ) -> None:
        with self._lock:
            self._classify_calls += 1
            self._classify_items += items
            self._classify_input_tokens += usage.input_tokens
            self._classify_output_tokens += usage.output_tokens
            self._classify_cache_read_tokens += usage.cache_read_tokens
            self._classify_cost_usd += usage.cost_usd
            self._classify_total_s += usage.duration_s
            self._classifier_dropped += classifier_dropped
            body = self._classify_body()
        self._display.update_body("classify_relevance", body=body)

    def classify_batch_failed(self, items: int) -> None:
        with self._lock:
            self._classify_failed += 1
            self._classify_items_errored += items
            body = self._classify_body()
        self._display.update_body("classify_relevance", body=body)

    # -----------------------------------------------------------------------
    # Judge-stage events
    # -----------------------------------------------------------------------

    def judge_enqueued(self) -> None:
        with self._lock:
            self._pending_judge += 1
            body = self._judge_body()
        self._display.update_body("judge_match", body=body)

    def judge_dequeued(self) -> None:
        with self._lock:
            self._pending_judge -= 1
            body = self._judge_body()
        self._display.update_body("judge_match", body=body)

    def judge_complete(self, usage: CallUsage, tier: MatchTier, source: str) -> None:
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
            if tier == MatchTier.green:
                self._green += 1
            elif tier == MatchTier.amber:
                self._amber += 1
            else:
                self._red += 1
            body = self._judge_body()
        self._display.update_body("judge_match", body=body)

    def judge_failed(self) -> None:
        with self._lock:
            self._judge_failed += 1
            self._judge_errored += 1
            judge_body = self._judge_body()
            pipeline_body = self._pipeline_body()
        self._display.update_body("judge_match", body=judge_body)
        self._display.update_body("pipeline", body=pipeline_body)

    # -----------------------------------------------------------------------
    # Output methods
    # -----------------------------------------------------------------------

    def format_run_divider(
        self, timestamp: str, tag: str | None, elapsed_s: float
    ) -> str:
        with self._lock:
            sources = dict(self._written_per_source)
            kept = self._written
            dedup_url_hits = self._dedup_url_hits
            dedup_tuple_hits = self._dedup_tuple_hits
            dedup_run_hits = self._dedup_run_hits
            dedup_misses = self._dedup_misses
            classify_calls = self._classify_calls
            classify_items = self._classify_items
            classify_total_s = self._classify_total_s
            judge_calls = self._judge_calls
            judge_total_s = self._judge_total_s
            claude_input_tokens = self._classify_input_tokens + self._judge_input_tokens
            claude_output_tokens = (
                self._classify_output_tokens + self._judge_output_tokens
            )
            claude_cache_read_tokens = (
                self._classify_cache_read_tokens + self._judge_cache_read_tokens
            )
            claude_cost_usd = self._classify_cost_usd + self._judge_cost_usd
            degraded_reason = self._degraded_reason
            classify_batches_failed = self._classify_failed
            classify_items_abandoned = self._classify_items_errored
            # Roll up classify-stage abandons into the judge-stage error total,
            # matching today's `judge_stats.errored += classify_stats.items_errored`
            # step that runs before the divider is formatted.
            judge_items_abandoned = self._judge_errored + self._classify_items_errored
            errors = judge_items_abandoned

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
                f"claude_input_tokens={claude_input_tokens}",
                f"claude_output_tokens={claude_output_tokens}",
                f"claude_cache_read_tokens={claude_cache_read_tokens}",
                f"claude_cost_usd={claude_cost_usd:.6f}",
                f"elapsed_s={elapsed_s:.1f}",
            ]
        )
        if degraded_reason is not None:
            parts.append(f"degraded_reason={degraded_reason}")
        if classify_batches_failed > 0:
            parts.append(f"classify_batches_failed={classify_batches_failed}")
        if classify_items_abandoned > 0:
            parts.append(f"classify_items_abandoned={classify_items_abandoned}")
        if judge_items_abandoned > 0:
            parts.append(f"judge_items_abandoned={judge_items_abandoned}")
        return f"<!-- {' '.join(parts)} -->\n"

    def to_run_summary(self, duration_s: float) -> RunSummary:
        with self._lock:
            claude_input_tokens = self._classify_input_tokens + self._judge_input_tokens
            claude_output_tokens = (
                self._classify_output_tokens + self._judge_output_tokens
            )
            claude_cache_read_tokens = (
                self._classify_cache_read_tokens + self._judge_cache_read_tokens
            )
            claude_cost_usd = self._classify_cost_usd + self._judge_cost_usd
            return RunSummary(
                duration_seconds=duration_s,
                discovered=self._discovered,
                skipped=self._skipped,
                prefilter_considered=self._prefilter_considered,
                prefilter_passed=self._prefilter_passed,
                prefilter_dropped=self._prefilter_dropped,
                prefilter_whitelist_hits=self._prefilter_whitelist_hits,
                prefilter_blacklist_hits=self._prefilter_blacklist_hits,
                prefilter_no_hit_either=self._prefilter_no_hit_either,
                dedup_url_hits=self._dedup_url_hits,
                dedup_tuple_hits=self._dedup_tuple_hits,
                dedup_run_hits=self._dedup_run_hits,
                dedup_misses=self._dedup_misses,
                classifier_dropped=self._classifier_dropped,
                written=self._written,
                green=self._green,
                amber=self._amber,
                red=self._red,
                enrich_failed=self._enrich_failed,
                external_redirects=self._external_redirects,
                errored=self._judge_errored + self._classify_items_errored,
                parsers_dead=self._parsers_dead,
                classify_items=self._classify_items,
                claude_input_tokens=claude_input_tokens,
                claude_output_tokens=claude_output_tokens,
                claude_cache_read_tokens=claude_cache_read_tokens,
                claude_cost_usd=claude_cost_usd,
            )

    def summarize_to_parser_log(self, started_at: datetime) -> None:
        with self._lock:
            classify_calls = self._classify_calls
            classify_items = self._classify_items
            classifier_dropped = self._classifier_dropped
            classify_failed = self._classify_failed
            classify_input_tokens = self._classify_input_tokens
            classify_output_tokens = self._classify_output_tokens
            classify_cache_read_tokens = self._classify_cache_read_tokens
            classify_cost_usd = self._classify_cost_usd
            classify_total_s = self._classify_total_s
            judge_calls = self._judge_calls
            judge_failed = self._judge_failed
            green = self._green
            amber = self._amber
            red = self._red
            judge_input_tokens = self._judge_input_tokens
            judge_output_tokens = self._judge_output_tokens
            judge_cache_read_tokens = self._judge_cache_read_tokens
            judge_cost_usd = self._judge_cost_usd
            judge_total_s = self._judge_total_s

        parser_log.summarize(
            "classify_relevance",
            {
                "batches_sent": classify_calls,
                "items_classified": classify_items,
                "in_domain": classify_items - classifier_dropped,
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
        parser_log.summarize(
            "judge_match",
            {
                "judges_sent": judge_calls,
                "judges_failed": judge_failed,
                "green": green,
                "amber": amber,
                "red": red,
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

    def _pipeline_body(self) -> str:
        return (
            f"discovered={self._discovered} written={self._written}"
            f" errors={self._enrich_failed + self._parsers_dead + self._judge_errored}"
        )

    def _dedup_body(self) -> str:
        return (
            f"url_hits={self._dedup_url_hits}"
            f" tuple_hits={self._dedup_tuple_hits}"
            f" run_hits={self._dedup_run_hits}"
            f" misses={self._dedup_misses}"
        )

    def _prefilter_body(self) -> str:
        return (
            f"considered={self._prefilter_considered}"
            f" passed={self._prefilter_passed}"
            f" dropped={self._prefilter_dropped}"
            f" (wl={self._prefilter_whitelist_hits} bl={self._prefilter_blacklist_hits})"
        )

    def _classify_body(self) -> str:
        result = (
            f"{self._classify_calls}/{self._total_batches} batches done"
            f" · {self._pending_classify} items in queue"
        )
        if self._classify_failed > 0:
            result += (
                f" · batches_failed={self._classify_failed}"
                f" items_errored={self._classify_items_errored}"
            )
        return result

    def _judge_body(self) -> str:
        result = (
            f"{self._judge_calls}/{self._judge_calls} judgments"
            f" · green={self._green} amber={self._amber} red={self._red}"
        )
        if self._judge_errored > 0:
            result += f" · errored={self._judge_errored}"
        result += f" · pending={self._pending_judge}"
        return result

    def _tally_prefilter_verdict(self, verdict: PreFilterVerdict) -> None:
        if verdict.whitelist_hit:
            self._prefilter_whitelist_hits += 1
        if verdict.blacklist_hit:
            self._prefilter_blacklist_hits += 1
        if not verdict.whitelist_hit and not verdict.blacklist_hit:
            self._prefilter_no_hit_either += 1
