from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline.content_gate import ContentSnapshot
from application_pipeline.dedup_counters import DedupSnapshot
from application_pipeline.freshness_gate import FreshnessSnapshot
from application_pipeline.llm.types import CallUsage
from application_pipeline.orchestrator import RunSummary
from application_pipeline.parser_log import RunLog
from application_pipeline.prefilter_gate import PreFilterSnapshot
from application_pipeline.run_metrics import (
    ClassifyBatchOutcomeObservation,
    ClassifyBatchFailureObservation,
    JudgeLifecycleFailureObservation,
    JudgeLifecycleOutcomeObservation,
    JudgeLifecycleStartObservation,
    RunMetrics,
)

ParserDropOutcome = Literal[
    "dedup_url_hit",
    "dedup_tuple_hit",
    "dedup_fuzzy_hit",
    "dedup_run_hit",
    "prefilter",
    "content_empty_body",
    "content_too_short",
]


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


def test_register_rows_creates_only_classify_row(
    run_log: RunLog,
) -> None:
    """register_rows no longer registers per-gate rows or the judge row; only the classify row."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    assert _registers(display) == [
        ("llm classify relevance", 1001, "running"),
    ]


def test_register_rows_classify_row_name_uses_spaces(
    run_log: RunLog,
) -> None:
    """The classify row name visible in the Status Display uses spaces, not underscores."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    names = [c.name for c in display.calls if c.method == "register"]
    assert "llm classify relevance" in names
    assert "llm_classify_relevance" not in names


# ---------------------------------------------------------------------------
# Parser-side events → pipeline row body
# ---------------------------------------------------------------------------


def test_pipeline_body_matches_main_stats_format(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

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
    metrics.register_rows()

    metrics.judge_failed()
    body = _last_body(display, "pipeline")
    assert "errors=1" in body


# ---------------------------------------------------------------------------
# Classify-stage events → classify_relevance row body
# ---------------------------------------------------------------------------


def test_classify_body_all_forwarded(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    metrics.classify_buffered(5)
    metrics.classify_batch_enqueued(5)
    metrics.classify_batch_dequeued(5)
    metrics.classify_batch_complete(usage, items=5, classifier_dropped=0)

    body = _last_body(display, "llm classify relevance")
    assert body == "5 forwarded"
    assert "dropped" not in body
    assert "queued" not in body


def test_classify_outcome_observation_updates_display_summary_divider_and_log(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage(
        input_tokens=500,
        output_tokens=200,
        cache_read_tokens=100,
        cost_usd=0.002,
        duration_s=2.5,
    )
    metrics.observe_classify_submission(5)
    metrics.observe_classify_batch_start(5)
    metrics.observe_classify_batch_outcome(
        ClassifyBatchOutcomeObservation(
            usage=usage,
            item_states=(
                "matched",
                "matched",
                "rejected",
                "retryable",
                "expired",
            ),
        )
    )

    assert _last_body(display, "llm classify relevance") == (
        "1 malformed · 2 dropped · 2 forwarded"
    )

    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert summary.classify_items == 5
    assert summary.classifier_dropped == 2
    assert summary.errored == 1
    assert summary.claude_input_tokens == 500
    assert summary.claude_output_tokens == 200
    assert summary.claude_cache_read_tokens == 100
    assert abs(summary.claude_cost_usd - 0.002) < 1e-9

    divider = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 3.0, dedup=DedupSnapshot()
    )
    assert "classify_calls=1" in divider
    assert "classify_items=5" in divider
    assert "classify_total_s=2.5" in divider
    assert "classify_input_tokens=500" in divider
    assert "classify_output_tokens=200" in divider
    assert "classify_cache_read_tokens=100" in divider
    assert "classify_cost_usd=0.002000" in divider
    assert "errors=1" in divider
    assert "classify_items_abandoned=1" in divider

    started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    metrics.summarize_to_parser_log(started_at)
    run_log_text = (tmp_path / "run.log").read_text()
    assert "batches_sent=1" in run_log_text
    assert "items_classified=5" in run_log_text
    assert "matched=2" in run_log_text
    assert "off_domain=2" in run_log_text
    assert "batches_failed=0" in run_log_text
    assert "input_tokens=500" in run_log_text


def test_classify_body_all_dropped_by_error(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.classify_buffered(3)
    metrics.classify_batch_enqueued(3)
    metrics.classify_batch_dequeued(3)
    metrics.classify_batch_failed(items=3)

    body = _last_body(display, "llm classify relevance")
    assert body == "3 malformed"
    assert "forwarded" not in body
    assert "queued" not in body
    assert "dropped" not in body


def test_classify_failure_observation_updates_display_summary_divider_and_log(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.observe_classify_submission(3)
    metrics.observe_classify_batch_start(3)
    metrics.observe_classify_batch_failure(ClassifyBatchFailureObservation(items=3))

    assert _last_body(display, "llm classify relevance") == "3 malformed"

    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert summary.classify_items == 0
    assert summary.errored == 3

    divider = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 3.0, dedup=DedupSnapshot()
    )
    assert "errors=3" in divider
    assert "classify_batches_failed=1" in divider
    assert "classify_items_abandoned=3" in divider

    started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    metrics.summarize_to_parser_log(started_at)
    run_log_text = (tmp_path / "run.log").read_text()
    assert "batches_sent=0" in run_log_text
    assert "items_classified=0" in run_log_text
    assert "matched=0" in run_log_text
    assert "off_domain=0" in run_log_text
    assert "batches_failed=1" in run_log_text


def test_classify_body_retryable_items_count_as_malformed_not_forwarded(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    metrics.classify_buffered(2)
    metrics.classify_batch_enqueued(2)
    metrics.classify_batch_dequeued(2)
    metrics.classify_batch_complete(
        usage,
        items=2,
        classifier_dropped=0,
        retryable_items=1,
    )

    body = _last_body(display, "llm classify relevance")
    assert body == "1 malformed · 1 forwarded"


def test_classify_body_queued_only_while_in_flight(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.classify_buffered(4)
    metrics.classify_buffered(2)

    body = _last_body(display, "llm classify relevance")
    assert body == "6 queued"


def test_classify_body_forwarded_cumulates_across_batches(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    for _ in range(2):
        metrics.classify_buffered(5)
        metrics.classify_batch_enqueued(5)
        metrics.classify_batch_dequeued(5)
        metrics.classify_batch_complete(usage, items=5, classifier_dropped=0)

    body = _last_body(display, "llm classify relevance")
    assert body == "10 forwarded"
    assert "queued" not in body


def test_classify_body_updates_per_buffered_call(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.classify_buffered(1)
    body_after_first = _last_body(display, "llm classify relevance")
    assert body_after_first == "1 queued"

    metrics.classify_buffered(1)
    body_after_second = _last_body(display, "llm classify relevance")
    assert body_after_second == "2 queued"


# ---------------------------------------------------------------------------
# Classify row — queued shows current depth, hidden when zero
# ---------------------------------------------------------------------------


def test_classify_queued_hidden_when_all_items_complete(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    metrics.classify_buffered(5)
    metrics.classify_batch_enqueued(5)
    metrics.classify_batch_dequeued(5)
    metrics.classify_batch_complete(usage, items=5, classifier_dropped=0)

    body = _last_body(display, "llm classify relevance")
    assert "queued" not in body


def test_classify_queued_hidden_when_all_items_errored(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.classify_buffered(3)
    metrics.classify_batch_enqueued(3)
    metrics.classify_batch_dequeued(3)
    metrics.classify_batch_failed(items=3)

    body = _last_body(display, "llm classify relevance")
    assert "queued" not in body


def test_classify_queued_decreases_as_batches_complete(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    metrics.classify_buffered(10)
    metrics.classify_batch_enqueued(5)
    metrics.classify_batch_dequeued(5)

    body_mid = _last_body(display, "llm classify relevance")
    assert "5 queued" in body_mid

    metrics.classify_batch_complete(usage, items=5, classifier_dropped=0)

    body_after = _last_body(display, "llm classify relevance")
    assert body_after == "5 queued · 5 forwarded"


# ---------------------------------------------------------------------------
# Classify row — queued/dropped/forwarded format
# ---------------------------------------------------------------------------


def test_classify_body_queued_dropped_forwarded_format(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    metrics.classify_buffered(5)
    metrics.classify_batch_enqueued(5)
    metrics.classify_batch_dequeued(5)
    metrics.classify_batch_complete(usage, items=5, classifier_dropped=2)

    body = _last_body(display, "llm classify relevance")
    assert body == "2 dropped · 3 forwarded"
    assert "queued" not in body
    assert "malformed" not in body


def test_classify_body_malformed_hidden_when_zero(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    metrics.classify_buffered(5)
    metrics.classify_batch_enqueued(5)
    metrics.classify_batch_dequeued(5)
    metrics.classify_batch_complete(usage, items=5, classifier_dropped=0)

    body = _last_body(display, "llm classify relevance")
    assert "malformed" not in body


def test_classify_body_malformed_and_dropped_both_present(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    # First batch: 2 items succeed, 1 classifier-dropped
    metrics.classify_buffered(3)
    metrics.classify_batch_enqueued(3)
    metrics.classify_batch_dequeued(3)
    metrics.classify_batch_complete(usage, items=3, classifier_dropped=1)
    # Second batch: 2 items fail with LLM error
    metrics.classify_buffered(2)
    metrics.classify_batch_enqueued(2)
    metrics.classify_batch_dequeued(2)
    metrics.classify_batch_failed(items=2)

    body = _last_body(display, "llm classify relevance")
    assert body == "2 malformed · 1 dropped · 2 forwarded"
    assert "queued" not in body


def test_classify_body_malformed_ordering_between_queued_and_dropped(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    # Queue some items that aren't yet dequeued
    metrics.classify_buffered(10)
    # Fail one batch that was already dequeued
    metrics.classify_batch_enqueued(3)
    metrics.classify_batch_dequeued(3)
    metrics.classify_batch_failed(items=3)
    # Complete one batch with a classifier drop
    usage = _make_usage()
    metrics.classify_batch_enqueued(3)
    metrics.classify_batch_dequeued(3)
    metrics.classify_batch_complete(usage, items=3, classifier_dropped=1)

    body = _last_body(display, "llm classify relevance")
    # queued shows remaining depth; malformed before dropped; forwarded last
    assert "queued" in body
    assert "malformed" in body
    assert "dropped" in body
    assert "forwarded" in body
    assert body.index("queued") < body.index("malformed")
    assert body.index("malformed") < body.index("dropped")
    assert body.index("dropped") < body.index("forwarded")


# ---------------------------------------------------------------------------
# Classify row — classifying (in-flight) segment
# ---------------------------------------------------------------------------


def test_classifying_segment_present_after_dequeue_before_complete(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.classify_buffered(5)
    metrics.classify_batch_enqueued(5)
    metrics.classify_batch_dequeued(5)

    body = _last_body(display, "llm classify relevance")
    assert "5 classifying" in body


def test_classifying_segment_absent_after_complete(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    metrics.classify_buffered(5)
    metrics.classify_batch_enqueued(5)
    metrics.classify_batch_dequeued(5)
    metrics.classify_batch_complete(usage, items=5, classifier_dropped=0)

    body = _last_body(display, "llm classify relevance")
    assert "classifying" not in body


def test_classifying_segment_absent_after_failed(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.classify_buffered(3)
    metrics.classify_batch_enqueued(3)
    metrics.classify_batch_dequeued(3)
    metrics.classify_batch_failed(items=3)

    body = _last_body(display, "llm classify relevance")
    assert "classifying" not in body


def test_classifying_segment_position_between_queued_and_dropped(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.classify_buffered(10)
    metrics.classify_batch_enqueued(5)
    metrics.classify_batch_dequeued(5)
    # 5 more still buffered but not yet dequeued — only the 5 dequeued are classifying

    body = _last_body(display, "llm classify relevance")
    assert body == "5 queued · 5 classifying"


def test_classifying_reflects_partial_completions(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    metrics.classify_buffered(6)
    metrics.classify_batch_enqueued(6)
    metrics.classify_batch_dequeued(6)
    metrics.classify_batch_complete(usage, items=3, classifier_dropped=0)

    body = _last_body(display, "llm classify relevance")
    assert "3 classifying" in body


# ---------------------------------------------------------------------------
# Judge-stage events — no persistent status row
# ---------------------------------------------------------------------------


def test_judge_lifecycle_outcome_updates_summary_divider_log_and_terminal_message(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage(
        input_tokens=300,
        output_tokens=150,
        cache_read_tokens=50,
        cost_usd=0.003,
        duration_s=1.5,
    )

    metrics.observe_judge_start(JudgeLifecycleStartObservation(candidate_count=5))
    metrics.observe_judge_outcome(
        JudgeLifecycleOutcomeObservation(
            usage=usage,
            card_count=3,
        )
    )

    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert summary.written == 3
    assert summary.errored == 0
    assert summary.claude_input_tokens == 300
    assert summary.claude_output_tokens == 150
    assert summary.claude_cache_read_tokens == 50
    assert abs(summary.claude_cost_usd - 0.003) < 1e-9

    divider = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 3.0, dedup=DedupSnapshot()
    )
    assert "kept=3" in divider
    assert "judge_calls=1" in divider
    assert "judge_total_s=1.5" in divider
    assert "judge_input_tokens=300" in divider
    assert "judge_output_tokens=150" in divider
    assert "judge_cache_read_tokens=50" in divider
    assert "judge_cost_usd=0.003000" in divider

    metrics.summarize_to_parser_log(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    run_log_text = (tmp_path / "run.log").read_text()
    assert "judges_sent=1" in run_log_text
    assert "judges_failed=0" in run_log_text
    assert "input_tokens=300" in run_log_text
    assert "output_tokens=150" in run_log_text
    assert "cache_read_tokens=50" in run_log_text
    assert "cost_usd=0.003" in run_log_text
    assert "duration_s=1.5" in run_log_text

    print_calls = [c for c in display.calls if c.method == "print"]
    assert len(print_calls) == 1
    assert print_calls[0].name == "llm_judge_match"
    assert "wrote 3 cards" in str(print_calls[0].kwargs["message"])


def test_judge_lifecycle_failure_updates_pipeline_summary_divider_and_log(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.observe_judge_start(JudgeLifecycleStartObservation(candidate_count=5))
    metrics.observe_judge_failure(JudgeLifecycleFailureObservation())

    assert _last_body(display, "pipeline") == "discovered=0 written=0 errors=1"

    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert summary.written == 0
    assert summary.errored == 1

    divider = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 3.0, dedup=DedupSnapshot()
    )
    assert "errors=1" in divider
    assert "judge_items_abandoned=1" in divider
    assert "judge_calls=0" in divider

    metrics.summarize_to_parser_log(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    run_log_text = (tmp_path / "run.log").read_text()
    assert "judges_sent=0" in run_log_text
    assert "judges_failed=1" in run_log_text


def test_judge_failed_increments_pipeline_error_count(run_log: RunLog) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.judge_failed()

    body = _last_body(display, "pipeline")
    assert "errors=1" in body


def test_judge_events_produce_no_llm_judge_match_body_updates(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_complete(usage, source="linkedin")

    judge_updates = display.body_updates_for("llm_judge_match")
    assert judge_updates == []


# ---------------------------------------------------------------------------
# Judge outcome — terminal log message
# ---------------------------------------------------------------------------


def test_judge_top_n_complete_prints_card_count_as_terminal_message(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)

    usage = _make_usage()
    metrics.judge_top_n_complete(usage, card_count=3)

    print_calls = [c for c in display.calls if c.method == "print"]
    assert len(print_calls) == 1
    assert "3" in str(print_calls[0].kwargs["message"])


# ---------------------------------------------------------------------------
# Judge row — lazily registered when candidates exist (issue #640)
# ---------------------------------------------------------------------------


def test_judge_row_registered_when_judge_started_with_candidates(
    run_log: RunLog,
) -> None:
    """judge_started(n) registers a judge row with phase 'running' and candidate count body."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.judge_started(14)

    register_calls = [
        c
        for c in display.calls
        if c.method == "register" and c.name == "llm judge match"
    ]
    assert len(register_calls) == 1
    assert register_calls[0].kwargs["phase"] == "running"
    assert "14" in str(register_calls[0].kwargs["body"])
    assert "candidates" in str(register_calls[0].kwargs["body"])


def test_judge_row_ordered_after_classify_row(run_log: RunLog) -> None:
    """Judge row order is greater than classify row order."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.judge_started(5)

    classify_reg = next(
        c
        for c in display.calls
        if c.method == "register" and c.name == "llm classify relevance"
    )
    judge_reg = next(
        c
        for c in display.calls
        if c.method == "register" and c.name == "llm judge match"
    )
    assert judge_reg.kwargs["order"] > classify_reg.kwargs["order"]


def test_judge_row_absent_when_judge_never_started(run_log: RunLog) -> None:
    """No judge row is registered when judge_started is never called (zero candidates)."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    assert "llm judge match" not in display.registered_names()


def test_judge_row_shows_card_count_and_done_on_completion(run_log: RunLog) -> None:
    """judge_top_n_complete updates body to show card count and transitions phase to done."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    usage = _make_usage()
    metrics.judge_started(5)
    metrics.judge_top_n_complete(usage, card_count=3)

    body = _last_body(display, "llm judge match")
    assert "3" in body
    assert "cards" in body

    phase_calls = [
        c
        for c in display.calls
        if c.method == "update_phase" and c.name == "llm judge match"
    ]
    assert phase_calls, "expected phase update for judge row on completion"
    assert phase_calls[-1].kwargs["phase"] == "done"


def test_judge_row_transitions_out_of_running_on_extractor_error(
    run_log: RunLog,
) -> None:
    """judge_top_n_failed transitions the judge row out of 'running'."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.judge_started(5)
    metrics.judge_top_n_failed()

    phase_calls = [
        c
        for c in display.calls
        if c.method == "update_phase" and c.name == "llm judge match"
    ]
    assert phase_calls, "expected phase update for judge row on failure"
    assert phase_calls[-1].kwargs["phase"] != "running"


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
    metrics.register_rows()

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


def test_emit_run_complete_writes_pipeline_orchestrator_row_from_metrics(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    display = FakeStatusDisplay()
    metrics = _build_populated_metrics(display, run_log)
    dedup = DedupSnapshot(
        dedup_url_hits=2,
        dedup_tuple_hits=3,
        dedup_run_hits=4,
        dedup_misses=5,
    )

    metrics.emit_run_complete(
        dedup=dedup,
        pool_size=6,
        daily_top_5_count=1,
        elapsed_s=12.34,
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "pipeline" / "orchestrator.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    run_complete_rows = [row for row in rows if row.get("event") == "run_complete"]
    assert run_complete_rows == [
        {
            "ts": run_complete_rows[0]["ts"],
            "event": "run_complete",
            "classify_calls": 1,
            "classify_input_tokens": 500,
            "classify_output_tokens": 200,
            "judge_input_tokens": 300,
            "judge_output_tokens": 150,
            "dedup_url_hits": 2,
            "dedup_tuple_hits": 3,
            "dedup_run_hits": 4,
            "dedup_misses": 5,
            "pool_size": 6,
            "daily_top_5_count": 1,
            "elapsed_s": 12.3,
        }
    ]


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


def test_concurrent_classify_observations_produce_correct_final_counts(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    n_threads = 6
    iters = 40
    usage = _make_usage(
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=2,
        cost_usd=0.001,
        duration_s=0.1,
    )

    def worker() -> None:
        for _ in range(iters):
            metrics.observe_classify_submission(3)
            metrics.observe_classify_batch_start(3)
            metrics.observe_classify_batch_outcome(
                ClassifyBatchOutcomeObservation(
                    usage=usage,
                    item_states=("matched", "rejected", "retryable"),
                )
            )
            metrics.observe_classify_submission(2)
            metrics.observe_classify_batch_start(2)
            metrics.observe_classify_batch_failure(
                ClassifyBatchFailureObservation(items=2)
            )

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    batches = n_threads * iters
    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert summary.classify_items == batches * 3
    assert summary.classifier_dropped == batches
    assert summary.errored == batches * 3
    assert summary.claude_input_tokens == batches * usage.input_tokens
    assert summary.claude_output_tokens == batches * usage.output_tokens

    body = _last_body(display, "llm classify relevance")
    assert body == f"{batches * 3} malformed · {batches} dropped · {batches} forwarded"

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert f"errors={batches * 3}" in result
    assert f"classify_calls={batches}" in result
    assert f"classify_items={batches * 3}" in result
    assert f"classify_batches_failed={batches}" in result
    assert f"classify_items_abandoned={batches * 3}" in result


# ---------------------------------------------------------------------------
# Per-parser counters in RunMetrics (new for issue #267)
# ---------------------------------------------------------------------------


def test_parser_summary_reflects_events_for_that_parser_id(run_log: RunLog) -> None:
    """discovered(parser_id) updates per-parser entry AND aggregate independently."""

    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

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
    metrics.register_rows()

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
    metrics.register_rows()

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

    body = _last_body(display, "parser p")
    assert "2 discovered" in body
    assert "queries" not in body
    assert "stubs" not in body


def test_parser_body_zero_drop_counters_hidden(run_log: RunLog) -> None:
    """Drop counters (freshness, dedup, pre-filter, content) are absent when zero."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=5)

    metrics.discovered("p")

    body = _last_body(display, "parser p")
    assert "freshness" not in body
    assert "dedup" not in body
    assert "pre-filter" not in body
    assert "content" not in body
    assert "enrich_failed" not in body


def test_parser_body_nonzero_drop_counters_go_to_gates_row(run_log: RunLog) -> None:
    """Gate drop counters appear in the gates row, not the parser row."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=2, total_queries=5, has_native_enrich=True)

    metrics.discovered("p")
    metrics.observe_parser_drop("p", outcome="freshness_discover")
    metrics.observe_parser_drop("p", outcome="freshness_discover")
    metrics.observe_parser_drop("p", outcome="freshness_post_enrich")
    metrics.observe_parser_drop("p", outcome="dedup_url_hit")
    metrics.observe_parser_enrich_failure("p")
    metrics.observe_parser_drop("p", outcome="prefilter")
    metrics.observe_parser_drop("p", outcome="content_empty_body")
    metrics.observe_parser_forwarded("p", "fallback")
    metrics.parser_done("p")

    parser_body = _last_body(display, "parser p")
    assert "freshness" not in parser_body
    assert "dedup" not in parser_body
    assert "pre-filter" not in parser_body
    assert "content" not in parser_body
    assert "1 enrich_failed" in parser_body
    assert "1 forwarded" in parser_body

    gates_body = _last_body(display, "parser p gates")
    assert "3 freshness" in gates_body
    assert "1 dedup" in gates_body
    assert "1 pre-filter" in gates_body
    assert "1 content" in gates_body
    assert "enrich_failed" not in gates_body


def test_parser_body_enrich_failed_hidden_without_native_enrich(
    run_log: RunLog,
) -> None:
    """enrich_failed counter is absent for parsers without has_native_enrich, even if nonzero."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=5, has_native_enrich=False)

    metrics.discovered("p")
    metrics.observe_parser_enrich_failure("p")
    metrics.parser_done("p")

    body = _last_body(display, "parser p")
    assert "enrich_failed" not in body


def test_parser_enrich_failure_observation_updates_pipeline_and_native_parser_once(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()
    metrics.register_parser(
        "native_parser", order=1, total_queries=1, has_native_enrich=True
    )

    metrics.observe_parser_enrich_failure("native_parser")

    summary = metrics.to_run_summary(
        1.0,
        PreFilterSnapshot(),
        FreshnessSnapshot(),
        ContentSnapshot(),
        DedupSnapshot(),
    )

    assert summary.enrich_failed == 1
    assert _last_body(display, "pipeline") == "discovered=0 written=0 errors=1"
    assert _last_body(display, "parser native parser") == (
        "0 discovered · 1 enrich_failed · 0 forwarded"
    )
    assert "parser native parser gates" not in display.registered_names()


def test_parser_enrich_failure_observation_keeps_counter_hidden_without_native_enrich(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()
    metrics.register_parser("fallback_parser", order=1, total_queries=1)

    metrics.observe_parser_enrich_failure("fallback_parser")

    summary = metrics.to_run_summary(
        1.0,
        PreFilterSnapshot(),
        FreshnessSnapshot(),
        ContentSnapshot(),
        DedupSnapshot(),
    )

    assert summary.enrich_failed == 1
    assert _last_body(display, "pipeline") == "discovered=0 written=0 errors=1"
    assert _last_body(display, "parser fallback parser") == "0 discovered · 0 forwarded"
    assert "parser fallback parser gates" not in display.registered_names()


def test_parser_body_forwarded_observation_updates_display_immediately(
    run_log: RunLog,
) -> None:
    """observe_parser_forwarded triggers a body update on the parser row immediately."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=2)

    metrics.discovered("p")
    before = len(display.body_updates_for("parser p"))
    metrics.observe_parser_forwarded("p", "fallback")
    after = len(display.body_updates_for("parser p"))

    assert after == before + 1
    body = _last_body(display, "parser p")
    assert "1 forwarded" in body


def test_parser_forwarded_observation_updates_parser_row_without_registering_gates(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=2, has_native_enrich=True)

    metrics.observe_parser_forwarded("p", "native")
    metrics.observe_parser_forwarded("p", "fallback")

    assert _last_body(display, "parser p") == "0 discovered · 2 forwarded"
    assert "parser p gates" not in display.registered_names()


def test_parser_done_sets_phase_column_to_done(run_log: RunLog) -> None:
    """parser_done() sets the phase column to 'done'; body does not contain '· done'."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=1)

    metrics.discovered("p")
    metrics.parser_done("p")

    phase_calls = [
        c for c in display.calls if c.method == "update_phase" and c.name == "parser p"
    ]
    assert phase_calls, "expected update_phase call for parser p"
    assert phase_calls[-1].kwargs["phase"] == "done"

    body = _last_body(display, "parser p")
    assert "· done" not in body


def test_parser_dead_sets_phase_column_to_dead(run_log: RunLog) -> None:
    """parser_dead() sets the phase column to 'dead'; body does not contain '· dead'."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=1, total_queries=1)

    metrics.discovered("p")
    metrics.parser_dead("p")

    phase_calls = [
        c for c in display.calls if c.method == "update_phase" and c.name == "parser p"
    ]
    assert phase_calls, "expected update_phase call for parser p"
    assert phase_calls[-1].kwargs["phase"] == "dead"

    body = _last_body(display, "parser p")
    assert "· dead" not in body


# ---------------------------------------------------------------------------
# Parser row / gates row split (issue #604)
# ---------------------------------------------------------------------------


def test_gates_row_absent_when_all_drop_counters_zero(run_log: RunLog) -> None:
    """No gates row is registered when there are no gate drops."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=2, total_queries=5)

    metrics.discovered("p")
    metrics.observe_parser_forwarded("p", "fallback")
    metrics.parser_done("p")

    registered = display.registered_names()
    assert "parser p gates" not in registered


def test_gates_row_appears_on_first_gate_drop(run_log: RunLog) -> None:
    """Gates row is registered when the first gate drop arrives."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=2, total_queries=5)

    metrics.discovered("p")
    assert "parser p gates" not in display.registered_names()

    metrics.observe_parser_drop("p", outcome="freshness_discover")
    assert "parser p gates" in display.registered_names()


def test_parser_drop_observation_maps_discover_and_post_enrich_freshness_to_one_gate_counter(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("jobs_beim_staat", order=4, total_queries=5)

    metrics.observe_parser_drop("jobs_beim_staat", outcome="freshness_discover")
    metrics.observe_parser_drop("jobs_beim_staat", outcome="freshness_post_enrich")

    assert _last_body(display, "parser jobs beim staat gates") == "2 freshness"


@pytest.mark.parametrize(
    ("outcome", "expected_body"),
    [
        ("dedup_url_hit", "1 dedup"),
        ("dedup_tuple_hit", "1 dedup"),
        ("dedup_fuzzy_hit", "1 dedup"),
        ("dedup_run_hit", "1 dedup"),
        ("prefilter", "1 pre-filter"),
        ("content_empty_body", "1 content"),
        ("content_too_short", "1 content"),
    ],
)
def test_parser_drop_observation_maps_each_outcome_to_its_gate_counter(
    run_log: RunLog,
    outcome: ParserDropOutcome,
    expected_body: str,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("jobs_beim_staat", order=4, total_queries=5)

    metrics.observe_parser_drop("jobs_beim_staat", outcome=outcome)

    assert _last_body(display, "parser jobs beim staat gates") == expected_body


def test_gates_row_pinned_at_parser_order_plus_one(run_log: RunLog) -> None:
    """Gates row order = parser order + 1."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=4, total_queries=5)

    metrics.observe_parser_drop("p", outcome="dedup_url_hit")

    register_calls = [
        c
        for c in display.calls
        if c.method == "register" and c.name == "parser p gates"
    ]
    assert len(register_calls) == 1
    assert register_calls[0].kwargs["order"] == 5


def test_gates_row_named_parser_type_gates(run_log: RunLog) -> None:
    """Gates row name is 'parser <type> gates' with spaces."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("bundesagentur", order=2, total_queries=5)

    metrics.observe_parser_drop("bundesagentur", outcome="freshness_discover")

    assert "parser bundesagentur gates" in display.registered_names()


def test_gates_row_name_uses_spaces_not_underscores(run_log: RunLog) -> None:
    """Parser type underscores become spaces in the gates row name."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("jobs_beim_staat", order=2, total_queries=5)

    metrics.observe_parser_drop("jobs_beim_staat", outcome="content_empty_body")

    assert "parser jobs beim staat gates" in display.registered_names()
    assert "parser jobs_beim_staat gates" not in display.registered_names()


def test_parser_done_mirrors_phase_to_gates_row(run_log: RunLog) -> None:
    """When parser transitions to done, gates row transitions to done too."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=2, total_queries=5)

    metrics.observe_parser_drop("p", outcome="freshness_discover")
    metrics.parser_done("p")

    phase_calls = [
        c
        for c in display.calls
        if c.method == "update_phase" and c.name == "parser p gates"
    ]
    assert phase_calls, "expected update_phase call for parser p gates"
    assert phase_calls[-1].kwargs["phase"] == "done"


def test_parser_dead_mirrors_phase_to_gates_row(run_log: RunLog) -> None:
    """When parser transitions to dead, gates row transitions to dead too."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=2, total_queries=5)

    metrics.observe_parser_drop("p", outcome="dedup_url_hit")
    metrics.parser_dead("p")

    phase_calls = [
        c
        for c in display.calls
        if c.method == "update_phase" and c.name == "parser p gates"
    ]
    assert phase_calls, "expected update_phase call for parser p gates"
    assert phase_calls[-1].kwargs["phase"] == "dead"


def test_gates_row_phase_not_mirrored_when_no_gate_drops(run_log: RunLog) -> None:
    """No phase update to gates row when parser completes with zero gate drops."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=2, total_queries=5)

    metrics.parser_done("p")

    phase_calls = [
        c
        for c in display.calls
        if c.method == "update_phase" and c.name == "parser p gates"
    ]
    assert phase_calls == []


def test_gates_row_body_updates_on_each_drop(run_log: RunLog) -> None:
    """Each subsequent gate drop updates the gates row body."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=2, total_queries=5)

    metrics.observe_parser_drop("p", outcome="freshness_discover")
    first_body = _last_body(display, "parser p gates")
    assert "1 freshness" in first_body

    metrics.observe_parser_drop("p", outcome="freshness_post_enrich")
    second_body = _last_body(display, "parser p gates")
    assert "2 freshness" in second_body


def test_parser_row_body_excludes_gate_drop_counters(run_log: RunLog) -> None:
    """Parser row body shows only discovered, optional enrich_failed, and forwarded — no gate counters."""
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=2, total_queries=5, has_native_enrich=True)

    metrics.discovered("p")
    metrics.observe_parser_drop("p", outcome="freshness_discover")
    metrics.observe_parser_drop("p", outcome="dedup_url_hit")
    metrics.observe_parser_drop("p", outcome="prefilter")
    metrics.observe_parser_drop("p", outcome="content_empty_body")
    metrics.observe_parser_forwarded("p", "fallback")

    body = _last_body(display, "parser p")
    assert "1 discovered" in body
    assert "1 forwarded" in body
    assert "freshness" not in body
    assert "dedup" not in body
    assert "pre-filter" not in body
    assert "content" not in body
