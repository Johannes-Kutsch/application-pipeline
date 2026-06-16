from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from application_pipeline import config as config_module
from application_pipeline import dedup as dedup_module
from application_pipeline.classify_stage import (
    BatchLLMEnricher,
    ClassifyStage,
    ClassifyStageHandoff,
)
from application_pipeline.llm import quota as _quota
from application_pipeline._context import current_stage
from application_pipeline.parser_log import RunLog
from application_pipeline.dedup_counters import DedupCounters
from application_pipeline.parser_lifecycle import (
    ParserLifecycleCollaborators,
    ParserLifecycleExecution,
    ParserLifecyclePlan,
    run_parser_lifecycle,
)
from application_pipeline.run_metrics import (
    JudgeLifecycleFailureObservation,
    JudgeLifecycleOutcomeObservation,
    JudgeLifecycleStartObservation,
    RunCompleteObservation,
    RunMetrics,
    RunSummary,
)
from application_pipeline.status_display import PlainStatusDisplay, StatusDisplay
from application_pipeline.config import ConfigError, SourceEntry
from application_pipeline.dedup import (
    DedupStoreError,
    DeduplicationStore,
)
from application_pipeline.extracts.card_store import CardStore, load_card_store
from application_pipeline.failure_report import (
    FailureReportWriter,
)
from application_pipeline.llm import (
    ClaudeExtractor,
    ExtractorError,
    JudgeCandidate,
    MatchVerdict,
)
from application_pipeline.llm.claude_cli import ClaudeUsageLimitError
from application_pipeline.llm.types import CallUsage
from application_pipeline.llm_enricher import LLMEnricher, LLMExtractor
from application_pipeline.pool import Pool
from application_pipeline.parsers import Parser
from application_pipeline.parsers.types import City, Location, Remote
from application_pipeline.parsers import registry as _default_registry
from application_pipeline.content_gate import ContentGate
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.prefilter_gate import PreFilterGate
from application_pipeline.prompts import PromptError, load_prompts
from application_pipeline.daily_results_file import DailyResultsFile, ResultsFileError
from application_pipeline.search_terms import SearchTerms, load_search_terms

_log = logging.getLogger(__name__)
__all__ = ["RunSummary", "current_stage", "run"]


def _has_native_enrich(cls: type) -> bool:
    # Class attribute wins (test doubles); fall back to module-level declaration.
    v = cls.__dict__.get("has_native_enrich")
    if v is not None:
        return bool(v)
    module = sys.modules.get(cls.__module__)
    return bool(getattr(module, "has_native_enrich", False))


@runtime_checkable
class _LLMJudge(Protocol):
    def judge_top_n(
        self, candidates: list[JudgeCandidate]
    ) -> tuple[list[MatchVerdict], CallUsage]: ...


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
    llm_enricher: object | None = None,
    extractor: object = None,
    card_store: CardStore | None = None,
    parser_registry: Callable[[str], type[Parser] | None] | None = None,
    dedup_store: DeduplicationStore | None = None,
    status_display: StatusDisplay | None = None,
    run_log: RunLog | None = None,
    stall_threshold_s: float = 60.0,
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
        failure_report_writer = FailureReportWriter(cfg.failures_path)

        # Step 9: Enter parsers via ExitStack, start parser threads, consume outbound queue
        metrics = RunMetrics(status_display, run_log=run_log)
        pool = Pool()
        _run_started_at = datetime.now(timezone.utc)

        locations: list[Location] = [City(loc) for loc in cfg.locations]
        if cfg.include_remote:
            locations.append(Remote())

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
                run_log=run_log,
            )
            content_gate = ContentGate(run_log=run_log)
            assert isinstance(llm_enricher, BatchLLMEnricher)
            classify_stage = ClassifyStage(
                batch_size=cfg.claude_classify_batch_size,
                parallelism=cfg.claude_classify_parallelism,
                pool_collector=pool,
                llm_enricher=llm_enricher,
                metrics=metrics,
                run_state=run_state,
                run_log=run_log,
                quota_wall=quota_wall,
            )

            classify_stage.start()
            classify_handoffs: dict[str, ClassifyStageHandoff] = {
                source.parser_type: classify_stage.handoff_for(
                    parser_id=source.parser_type,
                    metrics=metrics,
                )
                for _, source in parsers_list
            }
            run_parser_lifecycle(
                ParserLifecyclePlan(
                    parsers=[
                        ParserLifecycleExecution(
                            parser=parser,
                            parser_id=source.parser_type,
                            classify_handoff=classify_handoffs[source.parser_type],
                        )
                        for parser, source in parsers_list
                    ],
                    keywords=list(search_terms.keywords),
                    locations=locations,
                    collaborators=ParserLifecycleCollaborators(
                        run_log=run_log,
                        run_state=run_state,
                        freshness=freshness,
                        prefilter=prefilter,
                        content_gate=content_gate,
                        dedup=dedup_run,
                        dedup_counters=dedup_counters,
                        pool=pool,
                        metrics=metrics,
                        card_store=card_store,
                        failure_report_writer=failure_report_writer,
                        stall_threshold_s=stall_threshold_s,
                    ),
                )
            )

            freshness.emit_run_complete()
            freshness_snapshot = freshness.snapshot()
            prefilter.emit_run_complete()
            prefilter_snapshot = prefilter.snapshot()
            content_gate.emit_run_complete()
            content_snapshot = content_gate.snapshot()
            dedup_counters.emit_run_complete()
            dedup_snapshot = dedup_counters.snapshot()
            classify_stage.close()

        classify_completion = classify_stage.wait()

        if classify_completion.first_failure is not None:
            raise classify_completion.first_failure

        # Step 13: Single end-of-run judge_top_n call
        candidates = pool.judge_candidates(card_store)
        pool_size = pool.pool_size

        daily_top_5_count = 0
        if candidates and not no_judge:
            metrics.observe_judge_start(
                JudgeLifecycleStartObservation(candidate_count=len(candidates))
            )
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
                    now_utc = datetime.now(timezone.utc)
                    wake = _quota.compute_wake_time(err.reset_time, now_utc)
                    duration_s = max(0.0, (wake - now_utc).total_seconds())
                    run_log.event(
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
                    time.sleep(duration_s)
                except ExtractorError as exc:
                    _log.warning("judge_top_n failed: %s", exc)
                    run_log.event(
                        "llm_judge_top_n",
                        "error",
                        returncode=getattr(exc, "returncode", None),
                        stderr_excerpt=str(getattr(exc, "stderr", "") or "")[:200],
                        error=str(exc),
                    )
                    failure_report_writer.write_failure(
                        stage="judge_top_n",
                        error=exc,
                        log_tail="",
                    )
                    metrics.observe_judge_failure(JudgeLifecycleFailureObservation())
                    break

            if verdicts is not None and judge_usage is not None:
                try:
                    daily_top_5_count = pool.apply_match_verdicts(
                        verdicts,
                        card_store=card_store,
                        daily_results_file=daily_file,
                        dedup_store=dedup_store,
                    )
                except ResultsFileError as exc:
                    _log.error("daily file append failed: %s", exc)
                    raise
                metrics.observe_judge_outcome(
                    JudgeLifecycleOutcomeObservation(
                        usage=judge_usage,
                        card_count=daily_top_5_count,
                    )
                )
                run_log.event(
                    "pipeline_orchestrator",
                    "daily_file_written",
                    path=str(daily_file_path),
                    card_count=daily_top_5_count,
                )

        # Emit per-call-site SUMMARY OF SESSION trailers after judge metrics settle.
        metrics.summarize_to_parser_log(_run_started_at)

        elapsed_s = time.monotonic() - _start
        if run_state.degraded_reason is not None:
            metrics.set_degraded_reason(run_state.degraded_reason)

        metrics.emit_run_complete(
            RunCompleteObservation(
                dedup=dedup_snapshot,
                pool_size=pool_size,
                daily_top_5_count=daily_top_5_count,
                elapsed_s=elapsed_s,
            )
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
