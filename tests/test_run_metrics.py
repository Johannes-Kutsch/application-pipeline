from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline.content_gate import ContentSnapshot
from application_pipeline.dedup_counters import DedupSnapshot
from application_pipeline.freshness_gate import FreshnessSnapshot
from application_pipeline.llm.types import CallUsage
from application_pipeline.orchestrator import RunSummary
from application_pipeline.parser_log import RunLog
from application_pipeline.prefilter_gate import PreFilterSnapshot
from application_pipeline.run_metrics import RunMetrics


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


def _make_usage(
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 20,
    cost_usd: float = 0.001,
    duration_s: float = 1.0,
) -> CallUsage:
    return CallUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cost_usd=cost_usd,
        duration_s=duration_s,
    )


def _registers(display: FakeStatusDisplay) -> list[tuple[str, int, str]]:
    return [
        (c.name, c.kwargs["order"], c.kwargs["phase"])
        for c in display.calls
        if c.method == "register"
    ]


def _last_body(display: FakeStatusDisplay, row: str) -> str:
    updates = display.body_updates_for(row)
    assert updates, f"no body updates for row {row!r}"
    return updates[-1]


# ---------------------------------------------------------------------------
# register_rows
# ---------------------------------------------------------------------------


def test_register_rows_creates_only_classify_and_judge_rows(
    run_log: RunLog,
) -> None:
    """register_rows no longer registers per-gate rows; only llm rows."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(starting_order=10)

    registered = _registers(display)
    names = [r[0] for r in registered]
    assert "pipeline_prefilter" not in names
    assert "pipeline_freshness" not in names
    assert "pipeline_content" not in names
    assert "pipeline_dedup" not in names
    assert ("llm_classify_relevance", 11, "running") in registered
    assert ("llm_judge_match", 12, "running") in registered


def test_register_rows_starting_at_zero(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(starting_order=0)

    orders = [c.kwargs["order"] for c in display.calls if c.method == "register"]
    assert orders == [1, 2]


# ---------------------------------------------------------------------------
# Parser-side events → pipeline row body
# ---------------------------------------------------------------------------


def test_pipeline_body_matches_main_stats_format(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.discovered()
    metrics.discovered()
    metrics.enrich_failed()
    metrics.parser_dead()

    body = _last_body(display, "pipeline")
    # Matches _MainStats.pipeline_body(written=0, judge_errored=0):
    # discovered=2 written=0 errors=2 (enrich_failed=1 + parsers_dead=1 + judge_errored=0)
    assert body == "discovered=2 written=0 errors=2"


def test_pipeline_body_reflects_judge_errored(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.judge_failed()
    body = _last_body(display, "pipeline")
    assert "errors=1" in body


# ---------------------------------------------------------------------------
# Classify-stage events → classify_relevance row body
# ---------------------------------------------------------------------------


def test_classify_body_no_failures(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    usage = _make_usage()
    metrics.classify_buffered(5)
    metrics.classify_batch_enqueued(5)
    metrics.classify_batch_dequeued(5)
    metrics.classify_batch_complete(usage, items=5, classifier_dropped=2)

    body = _last_body(display, "llm_classify_relevance")
    assert body == "1/1 calls · 0 items in queue"
    assert "calls_failed" not in body
    assert "batches_failed" not in body


def test_classify_body_with_failures(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.classify_buffered(3)
    metrics.classify_batch_enqueued(3)
    metrics.classify_batch_dequeued(3)
    metrics.classify_batch_failed(items=3)

    body = _last_body(display, "llm_classify_relevance")
    assert "calls_failed=1 items_failed=3" in body
    assert "batches_failed" not in body
    assert "items_errored" not in body


def test_classify_body_pending_count(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.classify_buffered(4)
    metrics.classify_buffered(2)
    metrics.classify_batch_enqueued(4)

    body = _last_body(display, "llm_classify_relevance")
    assert "6 items in queue" in body


def test_classify_denominator_increments_at_dequeue_not_enqueue(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.classify_buffered(5)
    metrics.classify_batch_enqueued(5)

    body_after_enqueue = _last_body(display, "llm_classify_relevance")
    assert body_after_enqueue.startswith("0/0 calls")

    metrics.classify_batch_dequeued(5)

    body_after_dequeue = _last_body(display, "llm_classify_relevance")
    assert body_after_dequeue.startswith("0/1 calls")


def test_classify_numerator_increments_on_failure(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.classify_buffered(3)
    metrics.classify_batch_enqueued(3)
    metrics.classify_batch_dequeued(3)
    metrics.classify_batch_failed(items=3)

    body = _last_body(display, "llm_classify_relevance")
    assert body.startswith("1/1 calls")


def test_classify_idle_state_shows_n_over_n(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    usage = _make_usage()
    for _ in range(2):
        metrics.classify_buffered(5)
        metrics.classify_batch_enqueued(5)
        metrics.classify_batch_dequeued(5)
        metrics.classify_batch_complete(usage, items=5, classifier_dropped=0)

    body = _last_body(display, "llm_classify_relevance")
    assert body == "2/2 calls · 0 items in queue"


def test_classify_body_updates_per_item_without_batch_flush(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.classify_buffered(1)
    body_after_first = _last_body(display, "llm_classify_relevance")
    assert "1 items in queue" in body_after_first

    metrics.classify_buffered(1)
    body_after_second = _last_body(display, "llm_classify_relevance")
    assert "2 items in queue" in body_after_second


# ---------------------------------------------------------------------------
# Judge-stage events → judge_match row body
# ---------------------------------------------------------------------------


def test_judge_body_no_errors(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    usage = _make_usage()
    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_complete(usage, source="linkedin")
    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_complete(usage, source="indeed")

    body = _last_body(display, "llm_judge_match")
    assert body == "2/2 calls · 0 items in queue"
    assert "calls_failed" not in body


def test_judge_body_with_errors(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_failed()

    body = _last_body(display, "llm_judge_match")
    assert "calls_failed=1" in body


def test_judge_body_pending_count(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.judge_enqueued()
    metrics.judge_enqueued()

    body = _last_body(display, "llm_judge_match")
    assert "2 items in queue" in body


def test_judge_body_idle_steady_state_uses_calls_and_items_in_queue(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    usage = _make_usage()
    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_complete(usage, source="linkedin")

    body = _last_body(display, "llm_judge_match")
    assert body == "1/1 calls · 0 items in queue"


def test_judge_body_denominator_unchanged_before_dequeue(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.judge_enqueued()

    body = _last_body(display, "llm_judge_match")
    # denominator stays at 0 until the worker dequeues; queue increments to 1
    assert body == "0/0 calls · 1 items in queue"


def test_judge_body_dequeue_increments_denominator_and_decrements_queue(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.judge_enqueued()
    metrics.judge_dequeued()

    body = _last_body(display, "llm_judge_match")
    # denominator=1 (started), queue=0 (dequeued), numerator still 0 (in flight)
    assert body == "0/1 calls · 0 items in queue"


def test_judge_body_failure_increments_numerator_and_shows_calls_failed(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_failed()

    body = _last_body(display, "llm_judge_match")
    assert body == "1/1 calls · 0 items in queue · calls_failed=1"


# ---------------------------------------------------------------------------
# format_run_divider — byte-identical to _format_run_divider
# ---------------------------------------------------------------------------


def _format_run_divider(
    *,
    timestamp: str,
    tag: str | None,
    sources: dict[str, int],
    kept: int,
    errors: int,
    dedup_url_hits: int,
    dedup_tuple_hits: int,
    dedup_run_hits: int,
    dedup_misses: int,
    classify_calls: int,
    classify_items: int,
    classify_total_s: float,
    judge_calls: int,
    judge_total_s: float,
    classify_input_tokens: int,
    classify_output_tokens: int,
    classify_cache_read_tokens: int,
    classify_cost_usd: float,
    judge_input_tokens: int,
    judge_output_tokens: int,
    judge_cache_read_tokens: int,
    judge_cost_usd: float,
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
    return f"<!-- {' '.join(parts)} -->\n"


def _build_populated_metrics(display: FakeStatusDisplay, run_log: RunLog) -> RunMetrics:
    """Returns a RunMetrics with representative events covering all counter types."""
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    metrics.discovered()
    metrics.discovered()
    metrics.enrich_failed()
    metrics.parser_dead()

    classify_usage = _make_usage(
        input_tokens=500,
        output_tokens=200,
        cache_read_tokens=100,
        cost_usd=0.002,
        duration_s=2.5,
    )
    metrics.classify_buffered(2)
    metrics.classify_batch_enqueued(2)
    metrics.classify_batch_dequeued(2)
    metrics.classify_batch_complete(classify_usage, items=2, classifier_dropped=1)

    judge_usage = _make_usage(
        input_tokens=300,
        output_tokens=150,
        cache_read_tokens=50,
        cost_usd=0.003,
        duration_s=1.5,
    )
    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_complete(judge_usage, source="linkedin")

    return metrics


def test_format_run_divider_no_degraded_no_failures(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = _build_populated_metrics(display, run_log)

    timestamp = "2026-01-01T12:00:00Z"
    tag = "v1.2.3"
    elapsed_s = 42.7
    dedup = DedupSnapshot(
        dedup_url_hits=1,
        dedup_tuple_hits=1,
        dedup_run_hits=1,
        dedup_misses=2,
    )

    result = metrics.format_run_divider(timestamp, tag, elapsed_s, dedup=dedup)

    expected = _format_run_divider(
        timestamp=timestamp,
        tag=tag,
        sources={"linkedin": 1},
        kept=1,
        errors=0,  # divider errors = judge_errored; enrich/parser dead don't appear here
        dedup_url_hits=1,
        dedup_tuple_hits=1,
        dedup_run_hits=1,
        dedup_misses=2,
        classify_calls=1,
        classify_items=2,
        classify_total_s=2.5,
        judge_calls=1,
        judge_total_s=1.5,
        classify_input_tokens=500,
        classify_output_tokens=200,
        classify_cache_read_tokens=100,
        classify_cost_usd=0.002,
        judge_input_tokens=300,
        judge_output_tokens=150,
        judge_cache_read_tokens=50,
        judge_cost_usd=0.003,
        elapsed_s=elapsed_s,
    )
    assert result == expected


def test_format_run_divider_no_tag(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 10.0, dedup=DedupSnapshot()
    )
    assert "tag=" not in result
    assert result.startswith("<!-- run 2026-01-01T00:00:00Z")


def test_format_run_divider_no_sources(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 10.0, dedup=DedupSnapshot()
    )
    assert "sources=" not in result


def test_format_run_divider_with_sources(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    usage = _make_usage()
    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_complete(usage, "linkedin")

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 10.0, dedup=DedupSnapshot()
    )
    assert "sources=linkedin:1" in result


# ---------------------------------------------------------------------------
# format_run_divider — conditional fields
# ---------------------------------------------------------------------------


def test_degraded_reason_absent_by_default(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert "degraded_reason" not in result


def test_degraded_reason_present_after_set(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.set_degraded_reason("usage_limit")

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert "degraded_reason=usage_limit" in result


def test_classify_batches_failed_absent_when_zero(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert "classify_batches_failed" not in result


def test_classify_batches_failed_present_when_nonzero(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.classify_buffered(2)
    metrics.classify_batch_enqueued(2)
    metrics.classify_batch_dequeued(2)
    metrics.classify_batch_failed(items=2)

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert "classify_batches_failed=1" in result
    assert "classify_items_abandoned=2" in result


def test_classify_abandoned_items_roll_up_into_errors_and_judge_abandoned(
    run_log: RunLog,
) -> None:
    """Today the orchestrator does `judge_stats.errored += classify_stats.items_errored`
    before formatting the divider, so abandoned classify items count as errors
    and toward judge_items_abandoned. The module must preserve that roll-up."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.classify_buffered(3)
    metrics.classify_batch_enqueued(3)
    metrics.classify_batch_dequeued(3)
    metrics.classify_batch_failed(items=3)
    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_failed()

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert "errors=4" in result
    assert "judge_items_abandoned=4" in result
    assert "classify_items_abandoned=3" in result

    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert summary.errored == 4


def test_judge_items_abandoned_absent_when_zero(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert "judge_items_abandoned" not in result


def test_judge_items_abandoned_present_when_nonzero(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_failed()

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert "judge_items_abandoned=1" in result


def test_judge_resumed_absent_when_zero(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert "judge_resumed" not in result


def test_judge_resumed_present_when_nonzero(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot(judge_resumed=2)
    )
    assert "judge_resumed=2" in result


def test_format_run_divider_contains_per_callsite_token_fields(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = _build_populated_metrics(display, run_log)

    result = metrics.format_run_divider(
        "2026-01-01T12:00:00Z", "v1", 10.0, dedup=DedupSnapshot()
    )

    assert "classify_input_tokens=500" in result
    assert "classify_output_tokens=200" in result
    assert "classify_cache_read_tokens=100" in result
    assert "classify_cost_usd=0.002000" in result
    assert "judge_input_tokens=300" in result
    assert "judge_output_tokens=150" in result
    assert "judge_cache_read_tokens=50" in result
    assert "judge_cost_usd=0.003000" in result
    assert "claude_input_tokens" not in result
    assert "claude_output_tokens" not in result
    assert "claude_cache_read_tokens" not in result
    assert "claude_cost_usd" not in result


def test_format_run_divider_zero_callsite_tokens_when_no_calls(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )

    assert "classify_input_tokens=0" in result
    assert "classify_output_tokens=0" in result
    assert "classify_cache_read_tokens=0" in result
    assert "classify_cost_usd=0.000000" in result
    assert "judge_input_tokens=0" in result
    assert "judge_output_tokens=0" in result
    assert "judge_cache_read_tokens=0" in result
    assert "judge_cost_usd=0.000000" in result


# ---------------------------------------------------------------------------
# to_run_summary
# ---------------------------------------------------------------------------


def test_to_run_summary_shape_matches_runsummary(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = _build_populated_metrics(display, run_log)
    prefilter = PreFilterSnapshot(
        prefilter_considered=3,
        prefilter_passed=2,
        prefilter_dropped=1,
        prefilter_blacklist_hits=1,
    )
    summary = metrics.to_run_summary(
        duration_s=55.5,
        prefilter=prefilter,
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )

    assert isinstance(summary, RunSummary)
    assert summary.duration_seconds == 55.5
    assert summary.discovered == 2
    assert summary.prefilter_considered == 3
    assert summary.prefilter_passed == 2
    assert summary.prefilter_dropped == 1
    assert summary.prefilter_blacklist_hits == 1
    assert summary.classifier_dropped == 1
    assert summary.written == 1
    assert summary.enrich_failed == 1
    assert summary.errored == 0
    assert summary.parsers_dead == 1
    assert summary.classify_items == 2
    assert summary.claude_input_tokens == 800
    assert summary.claude_output_tokens == 350
    assert summary.claude_cache_read_tokens == 150
    assert abs(summary.claude_cost_usd - 0.005) < 1e-9


def test_to_run_summary_is_frozen(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )

    with pytest.raises((AttributeError, TypeError)):
        summary.discovered = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# summarize_to_parser_log
# ---------------------------------------------------------------------------


def test_summarize_to_parser_log_writes_classify_and_judge_summaries(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    display = FakeStatusDisplay()
    metrics = _build_populated_metrics(display, run_log)
    started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    metrics.summarize_to_parser_log(started_at)

    run_log_text = (tmp_path / "run.log").read_text()
    assert "SUMMARY OF SESSION" in run_log_text
    assert "batches_sent=1" in run_log_text
    assert "items_classified=2" in run_log_text
    assert "matched=1" in run_log_text
    assert "off_domain=1" in run_log_text
    assert "judges_sent=1" in run_log_text


def test_summarize_to_parser_log_uses_started_at_timestamp(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    started_at = datetime(2025, 6, 15, 8, 30, 0, tzinfo=timezone.utc)

    metrics.summarize_to_parser_log(started_at)

    run_log_text = (tmp_path / "run.log").read_text()
    assert "2025-06-15T08:30:00Z" in run_log_text


# ---------------------------------------------------------------------------
# Thread safety stress test
# ---------------------------------------------------------------------------


def test_concurrent_events_produce_correct_final_counts(run_log: RunLog) -> None:
    """All counter updates from concurrent threads must sum correctly."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    n_threads = 8
    iters = 50
    usage = _make_usage(
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=2,
        cost_usd=0.001,
        duration_s=0.1,
    )

    def worker() -> None:
        for _ in range(iters):
            metrics.discovered()
            metrics.enrich_failed()
            metrics.classify_buffered(1)
            metrics.classify_batch_enqueued(1)
            metrics.classify_batch_dequeued(1)
            metrics.classify_batch_complete(usage, items=1, classifier_dropped=0)
            metrics.judge_enqueued()
            metrics.judge_dequeued()
            metrics.judge_complete(usage, "src")

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = n_threads * iters
    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert summary.discovered == total
    assert summary.enrich_failed == total
    assert summary.written == total

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert f"kept={total}" in result
    assert f"classify_calls={total}" in result
    assert f"judge_calls={total}" in result


# ---------------------------------------------------------------------------
# Per-parser counters in RunMetrics (new for issue #267)
# ---------------------------------------------------------------------------


def test_parser_summary_reflects_events_for_that_parser_id(run_log: RunLog) -> None:
    """discovered(parser_id) updates per-parser entry AND aggregate independently."""

    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    started = time.monotonic()
    metrics.discovered("parser_a")
    metrics.discovered("parser_a")
    metrics.discovered("parser_b")
    end = time.monotonic()

    summary_a = metrics.parser_summary("parser_a", end, started)
    assert summary_a["discovered"] == 2

    summary_b = metrics.parser_summary("parser_b", end, started)
    assert summary_b["discovered"] == 1

    # Aggregate is unaffected
    run_summary = metrics.to_run_summary(
        1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert run_summary.discovered == 3


def test_parser_summary_key_set_is_exact(run_log: RunLog) -> None:
    """parser_summary returns exactly the required keys."""

    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    started = time.monotonic()
    metrics.discovered("p")
    end = time.monotonic()

    summary = metrics.parser_summary("p", end, started)
    assert set(summary.keys()) == {
        "discovered",
        "enrich_failed",
        "not_served_queries",
        "parsers_dead",
        "unparseable_dates",
        "duration",
    }


def test_parser_summary_all_events_tracked(run_log: RunLog) -> None:
    """All per-parser event methods update the right counter in parser_summary."""

    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    started = time.monotonic()
    metrics.discovered("p")
    metrics.enrich_failed("p")
    metrics.parser_dead("p")
    metrics.not_served_query("p")
    metrics.unparseable_date("p")
    end = time.monotonic()

    s = metrics.parser_summary("p", end, started)
    assert s["discovered"] == 1
    assert s["enrich_failed"] == 1
    assert s["parsers_dead"] == 1
    assert s["not_served_queries"] == 1
    assert s["unparseable_dates"] == 1
    assert isinstance(s["duration"], float)
    assert s["duration"] >= 0.0


def test_parser_summary_duration_rounded_to_one_decimal(run_log: RunLog) -> None:
    """duration = round(end - start, 1)."""

    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.discovered("p")

    started = 1000.0
    end = 1002.34567

    s = metrics.parser_summary("p", end, started)
    assert s["duration"] == round(end - started, 1)


def test_interleaved_parsers_produce_independent_per_parser_totals(
    run_log: RunLog,
) -> None:
    """Events for two parsers are tracked independently; aggregate is their sum."""

    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows(0)

    started = time.monotonic()
    for _ in range(3):
        metrics.discovered("alpha")
        metrics.enrich_failed("alpha")
    for _ in range(5):
        metrics.discovered("beta")
    end = time.monotonic()

    sa = metrics.parser_summary("alpha", end, started)
    sb = metrics.parser_summary("beta", end, started)

    assert sa["discovered"] == 3
    assert sa["enrich_failed"] == 3
    assert sb["discovered"] == 5
    assert sb["enrich_failed"] == 0

    summary = metrics.to_run_summary(
        1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert summary.discovered == 8
    assert summary.enrich_failed == 3


def test_parser_summary_unknown_parser_id_returns_zeros(run_log: RunLog) -> None:
    """parser_summary for a never-seen parser_id returns all-zero counts."""

    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    started = time.monotonic()
    end = time.monotonic()

    s = metrics.parser_summary("never_seen", end, started)
    assert s["discovered"] == 0
    assert s["enrich_failed"] == 0
    assert s["not_served_queries"] == 0
    assert s["parsers_dead"] == 0
    assert s["unparseable_dates"] == 0


# ---------------------------------------------------------------------------
# Parser body — new counter format (issue #588)
# ---------------------------------------------------------------------------


def test_parser_body_shows_discovered_and_forwarded_not_queries(
    run_log: RunLog,
) -> None:
    """Parser row body uses the new 'K discovered · F forwarded' format, not 'X/Y queries · K stubs'."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=5)

    metrics.discovered("p")
    metrics.discovered("p")

    body = _last_body(display, "parser_p")
    assert "2 discovered" in body
    assert "queries" not in body
    assert "stubs" not in body


def test_parser_body_zero_drop_counters_hidden(run_log: RunLog) -> None:
    """Drop counters (freshness, dedup, pre-filter, content) are absent when zero."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=5)

    metrics.discovered("p")

    body = _last_body(display, "parser_p")
    assert "freshness" not in body
    assert "dedup" not in body
    assert "pre-filter" not in body
    assert "content" not in body
    assert "enrich_failed" not in body


def test_parser_body_nonzero_drop_counters_appear_inline(run_log: RunLog) -> None:
    """Nonzero drop counters appear as named inline counters between discovered and forwarded."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=5, has_native_enrich=True)

    metrics.discovered("p")
    metrics.increment_freshness_dropped("p")
    metrics.increment_freshness_dropped("p")
    metrics.increment_freshness_dropped("p")
    metrics.increment_dedup_dropped("p")
    metrics.increment_prefilter_dropped("p")
    metrics.increment_enrich_failed_count("p")
    metrics.increment_content_dropped("p")
    metrics.increment_forwarded("p")
    # Trigger display update
    metrics.parser_done("p")

    body = _last_body(display, "parser_p")
    assert "3 freshness" in body
    assert "1 dedup" in body
    assert "1 pre-filter" in body
    assert "1 enrich_failed" in body
    assert "1 content" in body
    assert "1 forwarded" in body


def test_parser_body_enrich_failed_hidden_without_native_enrich(
    run_log: RunLog,
) -> None:
    """enrich_failed counter is absent for parsers without has_native_enrich, even if nonzero."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=5, has_native_enrich=False)

    metrics.discovered("p")
    metrics.increment_enrich_failed_count("p")
    metrics.parser_done("p")

    body = _last_body(display, "parser_p")
    assert "enrich_failed" not in body


def test_parser_body_forwarded_updates_display_immediately(run_log: RunLog) -> None:
    """increment_forwarded triggers a body update on the parser row immediately."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=2)

    metrics.discovered("p")
    before = len(display.body_updates_for("parser_p"))
    metrics.increment_forwarded("p")
    after = len(display.body_updates_for("parser_p"))

    assert after == before + 1
    body = _last_body(display, "parser_p")
    assert "1 forwarded" in body
