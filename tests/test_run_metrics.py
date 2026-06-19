from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

import pytest

from application_pipeline.content_gate import ContentSnapshot
from application_pipeline.dedup_counters import DedupSnapshot
from application_pipeline.freshness_gate import FreshnessSnapshot
from application_pipeline.llm.types import (
    AppliedClassifyItemOutcome,
    AppliedClassifyOutcome,
    CallUsage,
    MatchedListing,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import PositionStub
from application_pipeline.prefilter_gate import PreFilterSnapshot
from application_pipeline.run_metrics import (
    ClassifyBatchFailureObservation,
    ClassifyBatchOutcomeObservation,
    ClassifyBatchStartObservation,
    ClassifyRetryableObservation,
    ClassifyStageCompletionObservation,
    ClassifySubmissionObservation,
    ParserLifecycleObservation,
    RunMetrics,
    RunSummary,
)
from application_pipeline.status_display import PlainStatusDisplay
from tests.fake_status_display import FakeStatusDisplay


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


def _make_metrics(run_log: RunLog) -> RunMetrics:
    return RunMetrics(PlainStatusDisplay(run_log=run_log), run_log=run_log)


def _make_fake_display_metrics(tmp_path: Path) -> tuple[RunMetrics, FakeStatusDisplay]:
    display = FakeStatusDisplay()
    return RunMetrics(display, run_log=RunLog(tmp_path)), display


def _lifecycle_rows(tmp_path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (tmp_path / "lifecycle.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


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


def _observe_parser_enrich_failure(metrics: RunMetrics, parser_id: str) -> None:
    metrics.observe_parser_intake_enrich_failure(parser_id)


def _observe_parser_forwarded(
    metrics: RunMetrics, parser_id: str, mode: Literal["native", "fallback"]
) -> None:
    metrics.observe_parser_intake_forwarded(parser_id, mode)


def _observe_parser_drop(metrics: RunMetrics, parser_id: str, outcome: str) -> None:
    if outcome == "prefilter":
        metrics.observe_parser_intake_prefilter_drop(parser_id)
        return
    raise ValueError(f"unsupported parser drop outcome: {outcome}")


def _observe_classify_outcome(
    metrics: RunMetrics,
    usage: CallUsage,
    *,
    items: int,
    classifier_dropped: int,
    retryable_items: int = 0,
) -> None:
    forwarded = items - classifier_dropped - retryable_items
    item_states = cast(
        tuple[Literal["matched", "rejected", "retryable", "expired"], ...],
        ("matched",) * forwarded
        + ("rejected",) * classifier_dropped
        + ("retryable",) * retryable_items,
    )
    metrics.observe_classify_batch_outcome(
        ClassifyBatchOutcomeObservation(usage=usage, item_states=item_states)
    )


def _build_populated_metrics(run_log: RunLog) -> RunMetrics:
    metrics = _make_metrics(run_log)
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
    metrics.observe_classify_submission(2)
    metrics.observe_classify_batch_start(2)
    _observe_classify_outcome(metrics, classify_usage, items=2, classifier_dropped=1)

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


def test_register_rows_writes_classify_row_lifecycle_artifact(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)
    metrics = _make_metrics(run_log)

    metrics.register_rows()

    assert _lifecycle_rows(tmp_path) == [
        {
            "ts": _lifecycle_rows(tmp_path)[0]["ts"],
            "component": "llm classify relevance",
            "event": "registered",
            "order": 1001,
            "phase": "running",
        }
    ]


def test_register_parser_and_gate_rows_write_lifecycle_artifacts(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    metrics = _make_metrics(run_log)

    metrics.register_parser("jobs_beim_staat", order=4, total_queries=5)
    metrics.observe_parser_intake_content_drop("jobs_beim_staat", "empty_body")

    rows = _lifecycle_rows(tmp_path)
    assert [{k: v for k, v in row.items() if k != "ts"} for row in rows] == [
        {
            "component": "parser jobs beim staat",
            "event": "registered",
            "order": 4,
            "phase": "running",
        },
        {
            "component": "parser jobs beim staat gates",
            "event": "registered",
            "order": 5,
            "phase": "running",
        },
    ]


def test_parser_done_and_dead_write_phase_changes_to_lifecycle_log(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    metrics = _make_metrics(run_log)

    metrics.register_parser("alpha", order=2, total_queries=1)
    metrics.observe_parser_intake_freshness_drop("alpha", "discover")
    metrics.parser_done("alpha")

    metrics.register_parser("beta", order=6, total_queries=1)
    metrics.observe_parser_intake_dedup_drop("beta", "url_hit")
    metrics.parser_dead("beta")

    rows = _lifecycle_rows(tmp_path)
    assert any(
        row["component"] == "parser alpha"
        and row["event"] == "phase_changed"
        and row["phase"] == "done"
        for row in rows
    )
    assert any(
        row["component"] == "parser alpha gates"
        and row["event"] == "phase_changed"
        and row["phase"] == "done"
        for row in rows
    )
    assert any(
        row["component"] == "parser beta"
        and row["event"] == "phase_changed"
        and row["phase"] == "dead"
        for row in rows
    )
    assert any(
        row["component"] == "parser beta gates"
        and row["event"] == "phase_changed"
        and row["phase"] == "dead"
        for row in rows
    )


def test_classify_observation_objects_update_public_counters(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)
    metrics = _make_metrics(run_log)
    metrics.register_rows()

    metrics.observe_classify_submission(ClassifySubmissionObservation(count=2))
    metrics.observe_classify_batch_start(ClassifyBatchStartObservation(count=2))
    metrics.observe_classify_batch_failure(ClassifyBatchFailureObservation(items=2))
    metrics.observe_classify_stage_completion(ClassifyStageCompletionObservation())

    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert summary.classify_items == 0
    assert summary.errored == 2

    rows = _lifecycle_rows(tmp_path)
    assert any(
        row["component"] == "llm classify relevance"
        and row["event"] == "phase_changed"
        and row["phase"] == "done"
        for row in rows
    )


def test_classify_count_seam_preserves_queue_depth_in_flight_and_done_phase(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_rows()

    metrics.classify_submitted(3)
    assert display.body_updates_for("llm classify relevance")[-1] == "3 queued"

    metrics.classify_batch_started(2)
    assert (
        display.body_updates_for("llm classify relevance")[-1]
        == "1 queued · 2 classifying"
    )

    metrics.observe_classify_batch_outcome(
        ClassifyBatchOutcomeObservation(
            usage=_make_usage(),
            item_states=("matched", "rejected"),
        )
    )
    assert (
        display.body_updates_for("llm classify relevance")[-1]
        == "1 queued · 1 dropped · 1 forwarded"
    )

    metrics.classify_stage_completed()
    phase_updates = [
        call
        for call in display.calls
        if call.method == "update_phase" and call.name == "llm classify relevance"
    ]
    assert phase_updates[-1].kwargs["phase"] == "done"


def test_classify_outcome_observation_updates_summary_divider_and_log(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    metrics = _make_metrics(run_log)
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
            item_states=("matched", "matched", "rejected", "retryable", "expired"),
        )
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


def test_classify_failure_observation_updates_summary_divider_and_log(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    metrics = _make_metrics(run_log)
    metrics.register_rows()

    metrics.observe_classify_submission(3)
    metrics.observe_classify_batch_start(3)
    metrics.observe_classify_batch_failure(ClassifyBatchFailureObservation(items=3))

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


def test_classify_success_seam_updates_counters_parser_rows_and_outputs(
    tmp_path: Path,
) -> None:
    metrics, display = _make_fake_display_metrics(tmp_path)
    metrics.register_rows()
    metrics.register_parser(
        "parser.alpha", order=1, total_queries=1, has_native_enrich=True
    )
    metrics.register_parser(
        "parser.beta", order=3, total_queries=1, has_native_enrich=True
    )
    matched_stub = PositionStub(
        url="https://example.com/matched",
        title="Platform Engineer",
        source="example",
    )

    metrics.classify_submitted(5)
    metrics.classify_batch_started(5)
    metrics.classify_batch_succeeded(
        AppliedClassifyOutcome(
            items=[
                AppliedClassifyItemOutcome(
                    state="matched",
                    event_matches=True,
                    matched_listing=MatchedListing(listing_id=1, stub=matched_stub),
                ),
                AppliedClassifyItemOutcome(
                    state="rejected",
                    event_matches=False,
                ),
                AppliedClassifyItemOutcome(
                    state="retryable",
                    event_matches=None,
                ),
                AppliedClassifyItemOutcome(
                    state="expired",
                    event_matches=None,
                ),
                AppliedClassifyItemOutcome(
                    state="retryable",
                    event_matches=None,
                ),
            ],
            usage=_make_usage(
                input_tokens=500,
                output_tokens=200,
                cache_read_tokens=100,
                cost_usd=0.002,
                duration_s=2.5,
            ),
        ),
        parser_ids=(
            "parser.alpha",
            "parser.alpha",
            "parser.alpha",
            "parser.beta",
            "parser.beta",
        ),
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
    assert summary.enrich_failed == 2
    assert summary.errored == 2
    assert summary.claude_input_tokens == 500
    assert summary.claude_output_tokens == 200
    assert summary.claude_cache_read_tokens == 100
    assert abs(summary.claude_cost_usd - 0.002) < 1e-9

    assert display.body_updates_for("llm classify relevance")[-1] == (
        "2 malformed · 2 dropped · 1 forwarded"
    )
    assert display.body_updates_for("pipeline")[-1] == "discovered=0 written=0 errors=2"
    alpha_summary = metrics.parser_summary("parser.alpha", 0.0, 0.0)
    beta_summary = metrics.parser_summary("parser.beta", 0.0, 0.0)
    assert alpha_summary["enrich_failed"] == 1
    assert beta_summary["enrich_failed"] == 1

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
    assert "errors=2" in divider
    assert "classify_items_abandoned=2" in divider

    started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    metrics.summarize_to_parser_log(started_at)
    run_log_text = (tmp_path / "run.log").read_text()
    assert "batches_sent=1" in run_log_text
    assert "items_classified=5" in run_log_text
    assert "matched=1" in run_log_text
    assert "off_domain=2" in run_log_text
    assert "batches_failed=0" in run_log_text
    assert "input_tokens=500" in run_log_text


def test_classify_failure_seam_updates_summary_divider_and_log(tmp_path: Path) -> None:
    metrics, display = _make_fake_display_metrics(tmp_path)
    metrics.register_rows()

    metrics.classify_submitted(3)
    metrics.classify_batch_started(3)
    metrics.classify_batch_failed(3)

    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert summary.classify_items == 0
    assert summary.errored == 3
    assert display.body_updates_for("llm classify relevance")[-1] == "3 malformed"

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


def test_judge_lifecycle_outcome_updates_summary_divider_and_log(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    metrics = _make_metrics(run_log)
    metrics.register_rows()

    usage = _make_usage(
        input_tokens=300,
        output_tokens=150,
        cache_read_tokens=50,
        cost_usd=0.003,
        duration_s=1.5,
    )

    metrics.judge_started()
    metrics.judge_succeeded(usage, card_count=3)

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


def test_judge_success_prints_match_judge_terminal_message() -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=RunLog(Path("/tmp")))

    metrics.judge_started()
    metrics.judge_succeeded(_make_usage(), card_count=3)

    assert display.calls[-1].method == "print"
    assert display.calls[-1].name == "llm_judge_match"
    assert display.calls[-1].kwargs == {
        "message": "judge_top_n complete: wrote 3 cards"
    }


def test_judge_lifecycle_failure_updates_summary_divider_and_log(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    metrics = _make_metrics(run_log)
    metrics.register_rows()

    metrics.judge_started()
    metrics.judge_failed_lifecycle()

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


def test_format_run_divider_no_degraded_no_failures(run_log: RunLog) -> None:
    metrics = _build_populated_metrics(run_log)

    result = metrics.format_run_divider(
        "2026-01-01T12:00:00Z",
        "v1.2.3",
        42.7,
        dedup=DedupSnapshot(
            dedup_url_hits=1,
            dedup_tuple_hits=1,
            dedup_run_hits=1,
            dedup_misses=2,
        ),
    )

    assert result == (
        "<!-- run 2026-01-01T12:00:00Z tag=v1.2.3 sources=linkedin:1"
        " kept=1 errors=0 dedup_url_hits=1 dedup_tuple_hits=1 dedup_run_hits=1"
        " dedup_misses=2 classify_calls=1 classify_items=2 classify_total_s=2.5"
        " judge_calls=1 judge_total_s=1.5 classify_input_tokens=500"
        " classify_output_tokens=200 classify_cache_read_tokens=100"
        " classify_cost_usd=0.002000 judge_input_tokens=300"
        " judge_output_tokens=150 judge_cache_read_tokens=50"
        " judge_cost_usd=0.003000 elapsed_s=42.7 -->\n"
    )


def test_format_run_divider_conditional_fields(run_log: RunLog) -> None:
    metrics = _make_metrics(run_log)
    metrics.set_degraded_reason("usage_limit")
    metrics.observe_classify_submission(2)
    metrics.observe_classify_batch_start(2)
    metrics.observe_classify_batch_failure(ClassifyBatchFailureObservation(items=2))
    metrics.judge_enqueued()
    metrics.judge_dequeued()
    metrics.judge_failed()

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot(judge_resumed=2)
    )

    assert "tag=" not in result
    assert "sources=" not in result
    assert "degraded_reason=usage_limit" in result
    assert "classify_batches_failed=1" in result
    assert "classify_items_abandoned=2" in result
    assert "judge_items_abandoned=3" in result
    assert "judge_resumed=2" in result
    assert "errors=3" in result


def test_format_run_divider_contains_per_callsite_token_fields(run_log: RunLog) -> None:
    metrics = _build_populated_metrics(run_log)

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
    metrics = _make_metrics(run_log)

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
    metrics = _build_populated_metrics(run_log)

    metrics.emit_run_complete(
        dedup=DedupSnapshot(
            dedup_url_hits=2,
            dedup_tuple_hits=3,
            dedup_run_hits=4,
            dedup_misses=5,
        ),
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


def test_to_run_summary_shape_matches_runsummary(run_log: RunLog) -> None:
    metrics = _build_populated_metrics(run_log)
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
    metrics = _make_metrics(run_log)
    summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )

    with pytest.raises((AttributeError, TypeError)):
        summary.discovered = 99  # type: ignore[misc]


def test_summarize_to_parser_log_writes_classify_and_judge_summaries(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    metrics = _build_populated_metrics(run_log)

    metrics.summarize_to_parser_log(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

    run_log_text = (tmp_path / "run.log").read_text()
    assert "SUMMARY OF SESSION" in run_log_text
    assert "batches_sent=1" in run_log_text
    assert "items_classified=2" in run_log_text
    assert "matched=1" in run_log_text
    assert "off_domain=1" in run_log_text
    assert "judges_sent=1" in run_log_text


def test_summarize_to_parser_log_uses_started_at_timestamp(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)
    metrics = _make_metrics(run_log)

    metrics.summarize_to_parser_log(
        datetime(2025, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
    )

    run_log_text = (tmp_path / "run.log").read_text()
    assert "2025-06-15T08:30:00Z" in run_log_text


def test_concurrent_events_produce_correct_final_counts(run_log: RunLog) -> None:
    metrics = _make_metrics(run_log)

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
            metrics.observe_classify_submission(1)
            metrics.observe_classify_batch_start(1)
            _observe_classify_outcome(metrics, usage, items=1, classifier_dropped=0)
            metrics.judge_enqueued()
            metrics.judge_dequeued()
            metrics.judge_complete(usage, "src")

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

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
    metrics = _make_metrics(run_log)
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

    result = metrics.format_run_divider(
        "2026-01-01T00:00:00Z", None, 1.0, dedup=DedupSnapshot()
    )
    assert f"errors={batches * 3}" in result
    assert f"classify_calls={batches}" in result
    assert f"classify_items={batches * 3}" in result
    assert f"classify_batches_failed={batches}" in result
    assert f"classify_items_abandoned={batches * 3}" in result


def test_parser_summary_reflects_events_for_that_parser_id(run_log: RunLog) -> None:
    metrics = _make_metrics(run_log)
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

    run_summary = metrics.to_run_summary(
        1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )
    assert run_summary.discovered == 3


def test_parser_summary_key_set_is_exact(run_log: RunLog) -> None:
    metrics = _make_metrics(run_log)

    started = time.monotonic()
    metrics.discovered("p")
    end = time.monotonic()

    summary = metrics.parser_summary("p", end, started)
    assert set(summary.keys()) == {
        "discovered",
        "enrich_failed",
        "not_served_queries",
        "queries_done",
        "parsers_dead",
        "unparseable_dates",
        "duration",
    }


def test_parser_summary_all_events_tracked(run_log: RunLog) -> None:
    metrics = _make_metrics(run_log)
    metrics.register_rows()

    started = time.monotonic()
    metrics.discovered("p")
    metrics.enrich_failed("p")
    metrics.parser_dead("p")
    metrics.not_served_query("p")
    metrics.query_done("p")
    metrics.unparseable_date("p")
    end = time.monotonic()

    summary = metrics.parser_summary("p", end, started)
    assert summary["discovered"] == 1
    assert summary["enrich_failed"] == 1
    assert summary["parsers_dead"] == 1
    assert summary["not_served_queries"] == 1
    assert summary["queries_done"] == 1
    assert summary["unparseable_dates"] == 1
    assert isinstance(summary["duration"], float)
    assert summary["duration"] >= 0.0


def test_parser_lifecycle_observations_preserve_parser_summary_fields(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser("p", order=0, total_queries=2, has_native_enrich=False)

    started = time.monotonic()
    for event in (
        "discovered",
        "not_served_query",
        "query_done",
        "query_done",
        "parser_dead",
    ):
        metrics.observe_parser_lifecycle(
            ParserLifecycleObservation(parser_id="p", event=event)
        )
    end = time.monotonic()

    summary = metrics.parser_summary("p", end, started)
    assert summary["discovered"] == 1
    assert summary["enrich_failed"] == 0
    assert summary["not_served_queries"] == 1
    assert summary["queries_done"] == 2
    assert summary["parsers_dead"] == 1
    assert summary["unparseable_dates"] == 0
    assert isinstance(summary["duration"], float)


def test_parser_lifecycle_observations_preserve_done_and_dead_phase_updates(
    run_log: RunLog,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser(
        "done_parser", order=0, total_queries=1, has_native_enrich=False
    )
    metrics.register_parser(
        "dead_parser", order=2, total_queries=1, has_native_enrich=False
    )
    _observe_parser_drop(metrics, "done_parser", "prefilter")
    _observe_parser_drop(metrics, "dead_parser", "prefilter")

    metrics.observe_parser_lifecycle(
        ParserLifecycleObservation(parser_id="done_parser", event="parser_done")
    )
    metrics.observe_parser_lifecycle(
        ParserLifecycleObservation(parser_id="dead_parser", event="parser_dead")
    )

    done_phase_calls = [
        call
        for call in display.calls
        if call.method == "update_phase" and call.name == "parser done parser"
    ]
    dead_phase_calls = [
        call
        for call in display.calls
        if call.method == "update_phase" and call.name == "parser dead parser"
    ]

    assert done_phase_calls[-1].kwargs["phase"] == "done"
    assert dead_phase_calls[-1].kwargs["phase"] == "dead"
    assert any(
        call.method == "update_phase"
        and call.name == "parser done parser gates"
        and call.kwargs["phase"] == "done"
        for call in display.calls
    )
    assert any(
        call.method == "update_phase"
        and call.name == "parser dead parser gates"
        and call.kwargs["phase"] == "dead"
        for call in display.calls
    )


def test_parser_summary_duration_rounded_to_one_decimal(run_log: RunLog) -> None:
    metrics = _make_metrics(run_log)
    metrics.discovered("p")

    summary = metrics.parser_summary("p", 1002.34567, 1000.0)
    assert summary["duration"] == 2.3


def test_interleaved_parsers_produce_independent_per_parser_totals(
    run_log: RunLog,
) -> None:
    metrics = _make_metrics(run_log)
    metrics.register_rows()

    started = time.monotonic()
    for _ in range(3):
        metrics.discovered("alpha")
        metrics.enrich_failed("alpha")
    for _ in range(5):
        metrics.discovered("beta")
    end = time.monotonic()

    summary_alpha = metrics.parser_summary("alpha", end, started)
    summary_beta = metrics.parser_summary("beta", end, started)

    assert summary_alpha["discovered"] == 3
    assert summary_alpha["enrich_failed"] == 3
    assert summary_beta["discovered"] == 5
    assert summary_beta["enrich_failed"] == 0

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
    metrics = _make_metrics(run_log)

    started = time.monotonic()
    end = time.monotonic()

    summary = metrics.parser_summary("never_seen", end, started)
    assert summary["discovered"] == 0
    assert summary["enrich_failed"] == 0
    assert summary["not_served_queries"] == 0
    assert summary["queries_done"] == 0
    assert summary["parsers_dead"] == 0
    assert summary["unparseable_dates"] == 0


def test_parser_intake_observations_roll_up_into_public_counters(
    run_log: RunLog,
) -> None:
    metrics = _make_metrics(run_log)
    metrics.register_rows()
    metrics.register_parser(
        "parser.test", order=1, total_queries=1, has_native_enrich=True
    )

    _observe_parser_enrich_failure(metrics, "parser.test")
    _observe_parser_forwarded(metrics, "parser.test", "native")
    metrics.observe_classify_submission(1)
    metrics.observe_classify_batch_start(1)
    metrics.observe_classify_batch_outcome(
        ClassifyBatchOutcomeObservation(usage=_make_usage(), item_states=("retryable",))
    )
    metrics.observe_classify_retryable(
        ClassifyRetryableObservation(parser_id="parser.test")
    )

    summary = metrics.to_run_summary(
        1.0,
        PreFilterSnapshot(),
        FreshnessSnapshot(),
        ContentSnapshot(),
        DedupSnapshot(),
    )
    assert summary.enrich_failed == 2
    assert summary.errored == 1
    assert summary.classify_items == 1


def test_parser_gate_rows_sum_freshness_stages_and_hide_zero_counters(
    tmp_path: Path,
) -> None:
    metrics, display = _make_fake_display_metrics(tmp_path)
    metrics.register_parser("jobs_beim_staat", order=4, total_queries=5)

    metrics.observe_parser_intake_freshness_drop("jobs_beim_staat", "discover")
    metrics.observe_parser_intake_freshness_drop("jobs_beim_staat", "post_enrich")
    metrics.observe_parser_intake_content_drop("jobs_beim_staat", "too_short")

    assert display.body_updates_for("parser jobs beim staat gates") == [
        "1 freshness",
        "2 freshness",
        "2 freshness · 1 content",
    ]


def test_parser_row_body_keeps_native_enrich_failed_and_forwarded_semantics(
    tmp_path: Path,
) -> None:
    metrics, display = _make_fake_display_metrics(tmp_path)
    metrics.register_parser(
        "jobs_beim_staat", order=4, total_queries=5, has_native_enrich=True
    )

    metrics.observe_parser_intake_enrich_failure("jobs_beim_staat")
    metrics.observe_parser_intake_forwarded("jobs_beim_staat", "native")

    assert display.body_updates_for("parser jobs beim staat") == [
        "0/5 queries · 0 discovered · 0 forwarded",
        "0/5 queries · 0 discovered · 1 enrich_failed · 0 forwarded",
        "0/5 queries · 0 discovered · 1 enrich_failed · 1 forwarded",
    ]


def test_parser_row_body_preserves_query_progress_discovered_and_forwarded_activity(
    tmp_path: Path,
) -> None:
    metrics, display = _make_fake_display_metrics(tmp_path)
    metrics.register_parser("jobs_beim_staat", order=4, total_queries=3)

    metrics.observe_parser_lifecycle(
        ParserLifecycleObservation(
            parser_id="jobs_beim_staat",
            event="query_done",
        )
    )
    metrics.observe_parser_lifecycle(
        ParserLifecycleObservation(
            parser_id="jobs_beim_staat",
            event="discovered",
        )
    )
    metrics.observe_parser_intake_forwarded("jobs_beim_staat", "fallback")

    assert display.body_updates_for("parser jobs beim staat") == [
        "0/3 queries · 0 discovered · 0 forwarded",
        "1/3 queries · 0 discovered · 0 forwarded",
        "1/3 queries · 1 discovered · 0 forwarded",
        "1/3 queries · 1 discovered · 1 forwarded",
    ]


def test_parser_activity_reconciles_parser_summary_and_run_summary_totals(
    run_log: RunLog,
) -> None:
    metrics = _make_metrics(run_log)
    metrics.register_parser("alpha", order=0, total_queries=2, has_native_enrich=True)
    metrics.register_parser("beta", order=2, total_queries=1, has_native_enrich=False)

    metrics.observe_parser_lifecycle(
        ParserLifecycleObservation(parser_id="alpha", event="discovered")
    )
    metrics.observe_parser_intake_enrich_failure("alpha")
    metrics.observe_parser_lifecycle(
        ParserLifecycleObservation(parser_id="beta", event="discovered")
    )
    metrics.observe_parser_lifecycle(
        ParserLifecycleObservation(parser_id="beta", event="parser_dead")
    )

    alpha_summary = metrics.parser_summary(
        "alpha", end_monotonic=1.0, started_monotonic=0.0
    )
    beta_summary = metrics.parser_summary(
        "beta", end_monotonic=1.0, started_monotonic=0.0
    )
    run_summary = metrics.to_run_summary(
        duration_s=1.0,
        prefilter=PreFilterSnapshot(),
        freshness=FreshnessSnapshot(),
        content=ContentSnapshot(),
        dedup=DedupSnapshot(),
    )

    assert run_summary.discovered == (
        alpha_summary["discovered"] + beta_summary["discovered"]
    )
    assert run_summary.enrich_failed == (
        alpha_summary["enrich_failed"] + beta_summary["enrich_failed"]
    )
    assert run_summary.parsers_dead == (
        alpha_summary["parsers_dead"] + beta_summary["parsers_dead"]
    )


@pytest.mark.parametrize(
    ("kind", "detail", "expected_component"),
    [
        ("dedup", "url_hit", "parser jobs beim staat gates"),
        ("dedup", "tuple_hit", "parser jobs beim staat gates"),
        ("dedup", "fuzzy_hit", "parser jobs beim staat gates"),
        ("dedup", "run_hit", "parser jobs beim staat gates"),
        ("prefilter", None, "parser jobs beim staat gates"),
        ("content", "empty_body", "parser jobs beim staat gates"),
        ("content", "too_short", "parser jobs beim staat gates"),
    ],
)
def test_parser_drop_observations_register_gate_lifecycle_rows(
    tmp_path: Path,
    kind: str,
    detail: str | None,
    expected_component: str,
) -> None:
    run_log = RunLog(tmp_path)
    metrics = _make_metrics(run_log)
    metrics.register_parser("jobs_beim_staat", order=4, total_queries=5)

    if kind == "dedup":
        assert detail is not None
        metrics.observe_parser_intake_dedup_drop(
            "jobs_beim_staat",
            cast(Literal["url_hit", "tuple_hit", "fuzzy_hit", "run_hit"], detail),
        )
    elif kind == "prefilter":
        metrics.observe_parser_intake_prefilter_drop("jobs_beim_staat")
    else:
        assert detail is not None
        metrics.observe_parser_intake_content_drop(
            "jobs_beim_staat",
            cast(Literal["empty_body", "too_short"], detail),
        )

    rows = _lifecycle_rows(tmp_path)
    assert any(
        row["component"] == expected_component and row["event"] == "registered"
        for row in rows
    )
