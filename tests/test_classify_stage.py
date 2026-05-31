from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from application_pipeline.run_metrics import RunMetrics

from application_pipeline.classify_stage import (
    CLASSIFY_SHUTDOWN,
    ClassifyAccumulator,
    ClassifyDispatchItem,
    ClassifyQueueItem,
    ClassifyReadySubmission,
    ClassifyRequest,
    ClassifyStage,
)
from application_pipeline.llm import quota as _quota
from application_pipeline.llm.types import (
    AppliedClassifyOutcome,
    AppliedClassifyItemOutcome,
    CallUsage,
    MatchedListing,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import PositionStub
from fake_status_display import FakeStatusDisplay


def test_classify_stage_builds_classify_request_from_classify_ready_submission() -> (
    None
):
    stub = PositionStub(
        url="https://example.com/role",
        title="Platform Engineer",
        source="test",
    )
    submission = ClassifyReadySubmission(
        listing_id=7,
        stub=stub,
        raw_description="Raw description for classify handoff",
    )

    request = ClassifyRequest(submission=submission, parser_id="parser.test")

    assert request.submission.listing_id == 7
    assert request.submission.stub == stub
    assert request.submission.raw_description == "Raw description for classify handoff"
    assert request.parser_id == "parser.test"


class _RecordingMetrics:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def classify_batch_dequeued(self, size: int) -> None:
        self.batch_sizes.append(size)


class _RecordingRunState:
    def __init__(self) -> None:
        self.aborted_with: BaseException | None = None

    def set_aborted(self, exc: BaseException) -> None:
        self.aborted_with = exc


def _classify_request(listing_id: int) -> ClassifyRequest:
    return ClassifyRequest(
        submission=ClassifyReadySubmission(
            listing_id=listing_id,
            stub=PositionStub(
                url=f"https://example.com/role/{listing_id}",
                title=f"Platform Engineer {listing_id}",
                source="test",
            ),
            raw_description=f"Raw description {listing_id}",
        ),
        parser_id="parser.test",
    )


def _last_body(display: FakeStatusDisplay, name: str) -> str:
    return display.body_updates_for(name)[-1]


def _classify_event_rows(logs_dir: Path) -> list[dict[str, object]]:
    events_path = logs_dir / "llm" / "classify_relevance.events.jsonl"
    return [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_classify_stage_accumulator_fills_one_complete_batch_before_next() -> None:
    classify_queue: queue.Queue[ClassifyQueueItem] = queue.Queue()
    dispatch_queue: queue.Queue[ClassifyDispatchItem] = queue.Queue()
    metrics = _RecordingMetrics()
    run_state = _RecordingRunState()
    accumulator = ClassifyAccumulator(
        classify_queue=classify_queue,
        dispatch_queue=dispatch_queue,
        batch_size=3,
        num_workers=2,
        metrics=metrics,
        run_state=run_state,
    )

    accumulator.start()
    for listing_id in range(1, 6):
        classify_queue.put(_classify_request(listing_id))
    classify_queue.put(CLASSIFY_SHUTDOWN)
    accumulator.join(timeout=1)

    first_batch = dispatch_queue.get_nowait()
    second_batch = dispatch_queue.get_nowait()
    first_shutdown = dispatch_queue.get_nowait()
    second_shutdown = dispatch_queue.get_nowait()

    assert isinstance(first_batch, list)
    assert isinstance(second_batch, list)
    assert [request.submission.listing_id for request in first_batch] == [1, 2, 3]
    assert [request.submission.listing_id for request in second_batch] == [4, 5]
    assert first_shutdown is CLASSIFY_SHUTDOWN
    assert second_shutdown is CLASSIFY_SHUTDOWN
    assert metrics.batch_sizes == [3, 2]
    assert run_state.aborted_with is None


def test_classify_stage_accumulator_aborts_run_and_stops_workers_on_failure() -> None:
    classify_queue: queue.Queue[ClassifyQueueItem] = queue.Queue()
    dispatch_queue: queue.Queue[ClassifyDispatchItem] = queue.Queue()

    class _FailingMetrics:
        def classify_batch_dequeued(self, size: int) -> None:
            raise RuntimeError(f"boom {size}")

    run_state = _RecordingRunState()
    accumulator = ClassifyAccumulator(
        classify_queue=classify_queue,
        dispatch_queue=dispatch_queue,
        batch_size=1,
        num_workers=2,
        metrics=_FailingMetrics(),
        run_state=run_state,
    )

    accumulator.start()
    classify_queue.put(_classify_request(1))
    accumulator.join(timeout=1)

    first_shutdown = dispatch_queue.get_nowait()
    second_shutdown = dispatch_queue.get_nowait()

    assert isinstance(accumulator.exc, RuntimeError)
    assert str(accumulator.exc) == "boom 1"
    assert run_state.aborted_with is accumulator.exc
    assert first_shutdown is CLASSIFY_SHUTDOWN
    assert second_shutdown is CLASSIFY_SHUTDOWN


# ---------------------------------------------------------------------------
# ClassifyStage facade
# ---------------------------------------------------------------------------


class _FakeMetrics:
    def __init__(self) -> None:
        self.buffered = 0
        self.done = 0

    def classify_buffered(self, count: int) -> None:
        self.buffered += count

    def classify_batch_dequeued(self, size: int) -> None:
        pass

    def classify_batch_complete(
        self,
        usage: CallUsage,
        items: int,
        classifier_dropped: int,
        retryable_items: int = 0,
    ) -> None:
        pass

    def classify_batch_failed(self, items: int) -> None:
        pass

    def enrich_failed(self, parser_id: str = "") -> None:
        pass

    def classify_done(self) -> None:
        self.done += 1


class _FakeRunState:
    def __init__(self) -> None:
        self.aborted_with: BaseException | None = None

    @property
    def is_degraded(self) -> bool:
        return False

    def set_aborted(self, exc: BaseException) -> None:
        self.aborted_with = exc


class _FakePoolCollector:
    def add_matched(self, stub: PositionStub, listing_id: int) -> None:
        pass


class _RejectedEnricher:
    """Returns a rejected outcome for every item — no match, no exception."""

    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome:
        return AppliedClassifyOutcome(
            items=[
                AppliedClassifyItemOutcome(state="rejected", event_matches=False)
                for _ in items
            ]
        )


class _MatchedEnricher:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome:
        self.batch_sizes.append(len(items))
        return AppliedClassifyOutcome(
            items=[
                AppliedClassifyItemOutcome(
                    state="matched",
                    event_matches=True,
                    matched_listing=MatchedListing(listing_id=listing_id, stub=stub),
                )
                for listing_id, stub, _ in items
            ]
        )


class _BlockingMatchedEnricher:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.batch_sizes: list[int] = []

    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome:
        self.batch_sizes.append(len(items))
        self.started.set()
        self.release.wait(timeout=1)
        return AppliedClassifyOutcome(
            items=[
                AppliedClassifyItemOutcome(
                    state="matched",
                    event_matches=True,
                    matched_listing=MatchedListing(listing_id=listing_id, stub=stub),
                )
                for listing_id, stub, _ in items
            ]
        )


class _PausingMatchedEnricher:
    def __init__(self, expected_calls: int) -> None:
        self.expected_calls = expected_calls
        self.batch_sizes: list[int] = []
        self._lock = threading.Lock()
        self.started = threading.Event()
        self.release = threading.Event()

    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome:
        with self._lock:
            self.batch_sizes.append(len(items))
            if len(self.batch_sizes) >= self.expected_calls:
                self.started.set()
        self.release.wait(timeout=1)
        return AppliedClassifyOutcome(
            items=[
                AppliedClassifyItemOutcome(
                    state="matched",
                    event_matches=True,
                    matched_listing=MatchedListing(listing_id=listing_id, stub=stub),
                )
                for listing_id, stub, _ in items
            ]
        )


class _CollectingPoolCollector:
    def __init__(self) -> None:
        self.matched: list[tuple[int, PositionStub]] = []

    def add_matched(self, stub: PositionStub, listing_id: int) -> None:
        self.matched.append((listing_id, stub))


def _build_stage(
    *,
    logs_dir: Path,
    pool_collector: _CollectingPoolCollector,
    llm_enricher: object,
    metrics: object,
) -> ClassifyStage:
    return ClassifyStage(
        batch_size=1,
        parallelism=1,
        pool_collector=pool_collector,
        llm_enricher=llm_enricher,  # type: ignore[arg-type]
        metrics=metrics,  # type: ignore[arg-type]
        run_state=_FakeRunState(),
        run_log=RunLog(logs_dir),
        quota_wall=_quota.QuotaWall(),
    )


def _make_stage(tmp_path: Path, llm_enricher: object = None) -> ClassifyStage:
    return ClassifyStage(
        batch_size=1,
        parallelism=1,
        pool_collector=_FakePoolCollector(),
        llm_enricher=llm_enricher or _RejectedEnricher(),  # type: ignore[arg-type]
        metrics=_FakeMetrics(),
        run_state=_FakeRunState(),
        run_log=RunLog(tmp_path / "logs"),
        quota_wall=_quota.QuotaWall(),
    )


def test_classify_stage_empty_run_completes_with_no_failure(tmp_path: Path) -> None:
    stage = _make_stage(tmp_path)
    stage.start()
    stage.close()
    completion = stage.wait()
    assert completion.first_failure is None


def test_classify_stage_wait_without_explicit_close_still_completes(
    tmp_path: Path,
) -> None:
    stage = _make_stage(tmp_path)
    stage.start()
    completion = stage.wait()
    assert completion.first_failure is None


def test_classify_stage_close_is_idempotent(tmp_path: Path) -> None:
    stage = _make_stage(tmp_path)
    stage.start()
    stage.close()
    stage.close()
    completion = stage.wait()
    assert completion.first_failure is None


def test_classify_stage_worker_failure_surfaces_as_first_failure(
    tmp_path: Path,
) -> None:
    boom = RuntimeError("enricher exploded")

    class _ExplodingEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            raise boom

    metrics = _FakeMetrics()
    stage = _make_stage(tmp_path, llm_enricher=_ExplodingEnricher())
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)
    stage.start()
    handoff.submit(_classify_request(1))
    stage.close()
    completion = stage.wait()
    assert completion.first_failure is boom


def test_classify_stage_wait_flushes_partial_batch_and_marks_classify_done(
    tmp_path: Path,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=RunLog(tmp_path / "logs"))
    metrics.register_rows()
    pool_collector = _CollectingPoolCollector()
    llm_enricher = _BlockingMatchedEnricher()
    stage = ClassifyStage(
        batch_size=10,
        parallelism=4,
        pool_collector=pool_collector,
        llm_enricher=llm_enricher,
        metrics=metrics,
        run_state=_FakeRunState(),
        run_log=RunLog(tmp_path / "logs"),
        quota_wall=_quota.QuotaWall(),
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    for listing_id in range(1, 6):
        handoff.submit(_classify_request(listing_id))
    stage.close()

    completion_holder: list[object] = []
    waiter = threading.Thread(target=lambda: completion_holder.append(stage.wait()))
    waiter.start()

    assert llm_enricher.started.wait(timeout=1)
    assert waiter.is_alive()

    llm_enricher.release.set()
    waiter.join(timeout=1)

    assert waiter.is_alive() is False
    assert completion_holder[0] == stage.wait()
    assert llm_enricher.batch_sizes == [5]
    assert [listing_id for listing_id, _ in pool_collector.matched] == [1, 2, 3, 4, 5]
    assert any(
        call.method == "update_phase"
        and call.name == "llm classify relevance"
        and call.kwargs["phase"] == "done"
        for call in display.calls
    )


def test_classify_stage_batches_25_items_and_shows_in_flight_status_updates(
    tmp_path: Path,
) -> None:
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=RunLog(tmp_path / "logs"))
    metrics.register_rows()
    pool_collector = _CollectingPoolCollector()
    llm_enricher = _PausingMatchedEnricher(expected_calls=3)
    stage = ClassifyStage(
        batch_size=10,
        parallelism=4,
        pool_collector=pool_collector,
        llm_enricher=llm_enricher,
        metrics=metrics,
        run_state=_FakeRunState(),
        run_log=RunLog(tmp_path / "logs"),
        quota_wall=_quota.QuotaWall(),
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    for listing_id in range(1, 26):
        handoff.submit(_classify_request(listing_id))
    stage.close()

    assert llm_enricher.started.wait(timeout=1)
    assert llm_enricher.batch_sizes == [10, 10, 5]
    assert _last_body(display, "llm classify relevance") == "25 classifying"

    llm_enricher.release.set()
    completion = stage.wait()

    assert completion.first_failure is None
    assert len(pool_collector.matched) == 25
    assert _last_body(display, "llm classify relevance") == "25 forwarded"


def test_classify_stage_batches_exactly_10_items_into_one_llm_call(
    tmp_path: Path,
) -> None:
    pool_collector = _CollectingPoolCollector()
    llm_enricher = _MatchedEnricher()
    metrics = _FakeMetrics()
    stage = ClassifyStage(
        batch_size=10,
        parallelism=4,
        pool_collector=pool_collector,
        llm_enricher=llm_enricher,
        metrics=metrics,
        run_state=_FakeRunState(),
        run_log=RunLog(tmp_path / "logs"),
        quota_wall=_quota.QuotaWall(),
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    for listing_id in range(1, 11):
        handoff.submit(_classify_request(listing_id))
    completion = stage.wait()

    assert completion.first_failure is None
    assert llm_enricher.batch_sizes == [10]
    assert [listing_id for listing_id, _ in pool_collector.matched] == list(
        range(1, 11)
    )


def test_classify_stage_matched_outcome_routes_original_submission_to_pool_and_logs_match(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    pool_collector = _CollectingPoolCollector()

    class _MutatingMatchedEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, _, _ = items[0]
            return AppliedClassifyOutcome(
                items=[
                    AppliedClassifyItemOutcome(
                        state="matched",
                        event_matches=True,
                        matched_listing=MatchedListing(
                            listing_id=listing_id,
                            stub=PositionStub(
                                url="https://example.com/rewritten",
                                title="Rewritten title",
                                source="rewritten",
                            ),
                        ),
                    )
                ]
            )

    stage = _build_stage(
        logs_dir=logs_dir,
        pool_collector=pool_collector,
        llm_enricher=_MutatingMatchedEnricher(),
        metrics=_FakeMetrics(),
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=_FakeMetrics())
    request = _classify_request(1)

    stage.start()
    handoff.submit(request)
    completion = stage.wait()

    assert completion.first_failure is None
    assert pool_collector.matched == [
        (request.submission.listing_id, request.submission.stub)
    ]
    assert _classify_event_rows(logs_dir) == [
        {
            "ts": _classify_event_rows(logs_dir)[0]["ts"],
            "event": "classify_relevance",
            "matches": True,
        }
    ]


def test_classify_stage_rejected_outcome_skips_pool_and_counts_classifier_drop(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=RunLog(logs_dir))
    metrics.register_rows()
    pool_collector = _CollectingPoolCollector()

    class _RejectedSingleEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            return AppliedClassifyOutcome(
                items=[
                    AppliedClassifyItemOutcome(
                        state="rejected",
                        event_matches=False,
                    )
                ]
            )

    stage = _build_stage(
        logs_dir=logs_dir,
        pool_collector=pool_collector,
        llm_enricher=_RejectedSingleEnricher(),
        metrics=metrics,
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    handoff.submit(_classify_request(1))
    completion = stage.wait()

    assert completion.first_failure is None
    assert pool_collector.matched == []
    assert _last_body(display, "llm classify relevance") == "1 dropped"
    assert _classify_event_rows(logs_dir)[0]["matches"] is False


def test_classify_stage_expired_outcome_skips_pool_counts_drop_and_logs_matches_null(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=RunLog(logs_dir))
    metrics.register_rows()
    pool_collector = _CollectingPoolCollector()

    class _ExpiredSingleEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            return AppliedClassifyOutcome(
                items=[
                    AppliedClassifyItemOutcome(
                        state="expired",
                        event_matches=None,
                    )
                ]
            )

    stage = _build_stage(
        logs_dir=logs_dir,
        pool_collector=pool_collector,
        llm_enricher=_ExpiredSingleEnricher(),
        metrics=metrics,
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    handoff.submit(_classify_request(1))
    completion = stage.wait()

    assert completion.first_failure is None
    assert pool_collector.matched == []
    assert _last_body(display, "llm classify relevance") == "1 dropped"
    assert _classify_event_rows(logs_dir)[0]["matches"] is None


def test_classify_stage_retryable_outcome_skips_pool_and_counts_malformed(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=RunLog(logs_dir))
    metrics.register_rows()
    pool_collector = _CollectingPoolCollector()

    class _RetryableSingleEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            return AppliedClassifyOutcome(
                items=[
                    AppliedClassifyItemOutcome(
                        state="retryable",
                        event_matches=None,
                    )
                ]
            )

    stage = _build_stage(
        logs_dir=logs_dir,
        pool_collector=pool_collector,
        llm_enricher=_RetryableSingleEnricher(),
        metrics=metrics,
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    handoff.submit(_classify_request(1))
    completion = stage.wait()

    assert completion.first_failure is None
    assert pool_collector.matched == []
    assert _last_body(display, "llm classify relevance") == "1 malformed"
    assert _classify_event_rows(logs_dir)[0]["matches"] is None
