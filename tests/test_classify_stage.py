from __future__ import annotations

import json
import queue
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application_pipeline.run_metrics import RunMetrics

from application_pipeline.classify_stage import (
    CLASSIFY_SHUTDOWN,
    ClassifyAccumulator,
    ClassifyDispatchItem,
    ClassifyQueueItem,
    ClassifyReadySubmission,
    ClassifyRequest,
    ClassifyStageCompletion,
    ClassifyStage,
    ClassifyStageHandoff,
)
from application_pipeline.llm.claude_cli import ClaudeUsageLimitError
from application_pipeline.llm import quota as _quota
from application_pipeline.llm.types import (
    AppliedClassifyOutcome,
    AppliedClassifyItemOutcome,
    CallUsage,
    ExtractorBatchMalformedError,
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


def test_classify_stage_handoff_submit_ready_routes_listing_through_stage(
    tmp_path: Path,
) -> None:
    stub = PositionStub(
        url="https://example.com/role",
        title="Platform Engineer",
        source="test",
    )
    pool_collector = _CollectingPoolCollector()
    metrics = _FakeMetrics()
    llm_enricher = _MatchedEnricher()
    stage = _build_stage(
        logs_dir=tmp_path / "logs",
        pool_collector=pool_collector,
        llm_enricher=llm_enricher,
        metrics=metrics,
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    handoff.submit_ready(
        listing_id=7,
        stub=stub,
        raw_description="Raw description for classify handoff",
        parser_id="parser.test",
    )
    stage.close()
    completion = stage.wait()

    assert completion.first_failure is None
    assert metrics.buffered == 1
    assert llm_enricher.batch_sizes == [1]
    assert pool_collector.matched == [(7, stub)]


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


def _classify_ready_facts(
    listing_id: int,
) -> tuple[int, PositionStub, str, str]:
    request = _classify_request(listing_id)
    return (
        request.submission.listing_id,
        request.submission.stub,
        request.submission.raw_description,
        request.parser_id,
    )


def _submit_ready(handoff: ClassifyStageHandoff, listing_id: int) -> None:
    ready_listing_id, stub, raw_description, parser_id = _classify_ready_facts(
        listing_id
    )
    handoff.submit_ready(
        listing_id=ready_listing_id,
        stub=stub,
        raw_description=raw_description,
        parser_id=parser_id,
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


def _pipeline_event_rows(logs_dir: Path) -> list[dict[str, object]]:
    events_path = logs_dir / "pipeline" / "orchestrator.events.jsonl"
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


class _FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self.sleep_calls: list[float] = []
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleep_calls.append(seconds)
            self._now += timedelta(seconds=seconds)


class _UsageLimitThenMatchedEnricher:
    def __init__(self, *, reset_time: datetime) -> None:
        self._reset_time = reset_time
        self.calls = 0

    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome:
        self.calls += 1
        if self.calls == 1:
            raise ClaudeUsageLimitError(
                "quota hit",
                returncode=1,
                stdout="",
                stderr="",
                envelope=None,
                reset_time=self._reset_time,
            )
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


class _ControllableSleepClock:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self.sleep_calls: list[float] = []
        self.sleep_started = threading.Event()
        self.allow_sleep = threading.Event()
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        self.sleep_started.set()
        self.allow_sleep.wait(timeout=1)
        with self._lock:
            self.sleep_calls.append(seconds)
            self._now += timedelta(seconds=seconds)


class _ParallelQuotaWallEnricher:
    def __init__(self, *, reset_time: datetime) -> None:
        self._reset_time = reset_time
        self.calls: list[int] = []
        self.wall_raised = threading.Event()
        self.release_second_batch = threading.Event()
        self.third_batch_started = threading.Event()
        self._lock = threading.Lock()
        self._quota_seen = False

    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome:
        listing_id, stub, _ = items[0]
        with self._lock:
            self.calls.append(listing_id)
            if listing_id == 1 and not self._quota_seen:
                self._quota_seen = True
                self.wall_raised.set()
                raise ClaudeUsageLimitError(
                    "quota hit",
                    returncode=1,
                    stdout="",
                    stderr="",
                    envelope=None,
                    reset_time=self._reset_time,
                )
        if listing_id == 2:
            assert self.wall_raised.wait(timeout=1)
            self.release_second_batch.wait(timeout=1)
        if listing_id == 3:
            self.third_batch_started.set()
        return AppliedClassifyOutcome(
            items=[
                AppliedClassifyItemOutcome(
                    state="matched",
                    event_matches=True,
                    matched_listing=MatchedListing(listing_id=listing_id, stub=stub),
                )
            ]
        )


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


def test_classify_stage_wait_requires_close_to_flush_partial_batch(
    tmp_path: Path,
) -> None:
    llm_enricher = _BlockingMatchedEnricher()
    stage = ClassifyStage(
        batch_size=10,
        parallelism=1,
        pool_collector=_CollectingPoolCollector(),
        llm_enricher=llm_enricher,
        metrics=_FakeMetrics(),
        run_state=_FakeRunState(),
        run_log=RunLog(tmp_path / "logs"),
        quota_wall=_quota.QuotaWall(),
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=_FakeMetrics())
    completion_holder: list[ClassifyStageCompletion] = []

    stage.start()
    for listing_id in range(1, 6):
        _submit_ready(handoff, listing_id)

    waiter = threading.Thread(target=lambda: completion_holder.append(stage.wait()))
    waiter.start()

    assert llm_enricher.started.wait(timeout=0.2) is False
    assert waiter.is_alive()

    stage.close()

    assert llm_enricher.started.wait(timeout=1)
    llm_enricher.release.set()
    waiter.join(timeout=1)

    assert waiter.is_alive() is False
    assert completion_holder[0].first_failure is None
    assert llm_enricher.batch_sizes == [5]


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
    _submit_ready(handoff, 1)
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
        _submit_ready(handoff, listing_id)
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
        _submit_ready(handoff, listing_id)
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
        _submit_ready(handoff, listing_id)
    stage.close()
    completion = stage.wait()

    assert completion.first_failure is None
    assert llm_enricher.batch_sizes == [10]
    assert [listing_id for listing_id, _ in pool_collector.matched] == list(
        range(1, 11)
    )


def test_classify_stage_handoff_fills_complete_batch_before_tail_flush_at_stage_seam(
    tmp_path: Path,
) -> None:
    pool_collector = _CollectingPoolCollector()

    class _RecordingOrderedEnricher:
        def __init__(self) -> None:
            self.calls: list[list[int]] = []
            self._lock = threading.Lock()
            self._first_batch_released = threading.Event()

        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_ids = [listing_id for listing_id, _, _ in items]
            with self._lock:
                self.calls.append(listing_ids)
            if listing_ids == [1, 2, 3]:
                self._first_batch_released.wait(timeout=1)
            else:
                self._first_batch_released.set()
            return AppliedClassifyOutcome(
                items=[
                    AppliedClassifyItemOutcome(
                        state="matched",
                        event_matches=True,
                        matched_listing=MatchedListing(
                            listing_id=listing_id,
                            stub=PositionStub(
                                url=f"https://example.com/rewritten/{listing_id}",
                                title=f"Rewritten title {listing_id}",
                                source="rewritten",
                            ),
                        ),
                    )
                    for listing_id, _, _ in items
                ]
            )

    llm_enricher = _RecordingOrderedEnricher()
    metrics = _FakeMetrics()
    stage = ClassifyStage(
        batch_size=3,
        parallelism=2,
        pool_collector=pool_collector,
        llm_enricher=llm_enricher,
        metrics=metrics,
        run_state=_FakeRunState(),
        run_log=RunLog(tmp_path / "logs"),
        quota_wall=_quota.QuotaWall(),
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    expected_pairs: list[tuple[int, PositionStub]] = []
    for listing_id in range(1, 6):
        listing_id_value, stub, raw_description, parser_id = _classify_ready_facts(
            listing_id
        )
        expected_pairs.append((listing_id_value, stub))
        handoff.submit_ready(
            listing_id=listing_id_value,
            stub=stub,
            raw_description=raw_description,
            parser_id=parser_id,
        )
    stage.close()
    completion = stage.wait()

    assert completion.first_failure is None
    assert llm_enricher.calls == [[1, 2, 3], [4, 5]]
    assert sorted(pool_collector.matched, key=lambda item: item[0]) == expected_pairs


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
    listing_id, stub, raw_description, parser_id = _classify_ready_facts(1)

    stage.start()
    handoff.submit_ready(
        listing_id=listing_id,
        stub=stub,
        raw_description=raw_description,
        parser_id=parser_id,
    )
    stage.close()
    completion = stage.wait()

    assert completion.first_failure is None
    assert pool_collector.matched == [(listing_id, stub)]
    rows = _classify_event_rows(logs_dir)
    assert len(rows) == 1
    assert rows[0]["event"] == "classify_relevance"
    assert rows[0]["matches"] is True


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
    _submit_ready(handoff, 1)
    stage.close()
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
    _submit_ready(handoff, 1)
    stage.close()
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
    _submit_ready(handoff, 1)
    stage.close()
    completion = stage.wait()

    assert completion.first_failure is None
    assert pool_collector.matched == []
    assert _last_body(display, "llm classify relevance") == "1 malformed"
    assert _classify_event_rows(logs_dir)[0]["matches"] is None


def test_classify_stage_batch_level_malformed_failure_skips_failed_batch_and_continues(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=RunLog(logs_dir))
    metrics.register_rows()
    pool_collector = _CollectingPoolCollector()

    class _MalformedThenMatchedEnricher:
        def __init__(self) -> None:
            self.calls = 0

        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            self.calls += 1
            if self.calls == 1:
                raise ExtractorBatchMalformedError("bad batch verdict")
            return AppliedClassifyOutcome(
                items=[
                    AppliedClassifyItemOutcome(
                        state="matched",
                        event_matches=True,
                        matched_listing=MatchedListing(
                            listing_id=listing_id, stub=stub
                        ),
                    )
                    for listing_id, stub, _ in items
                ]
            )

    stage = ClassifyStage(
        batch_size=2,
        parallelism=1,
        pool_collector=pool_collector,
        llm_enricher=_MalformedThenMatchedEnricher(),
        metrics=metrics,
        run_state=_FakeRunState(),
        run_log=RunLog(logs_dir),
        quota_wall=_quota.QuotaWall(),
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    _submit_ready(handoff, 1)
    _submit_ready(handoff, 2)
    _submit_ready(handoff, 3)
    stage.close()
    completion = stage.wait()

    assert completion.first_failure is None
    assert [listing_id for listing_id, _ in pool_collector.matched] == [3]
    assert _last_body(display, "llm classify relevance") == "2 malformed · 1 forwarded"

    rows = _classify_event_rows(logs_dir)
    assert rows == [
        {
            "ts": rows[0]["ts"],
            "event": "classify_relevance",
            "status": "error",
            "error": "bad batch verdict",
        },
        {
            "ts": rows[1]["ts"],
            "event": "classify_relevance",
            "matches": True,
        },
    ]


def test_classify_stage_retries_quota_limited_batch_after_wall_sleep(
    tmp_path: Path, monkeypatch
) -> None:
    logs_dir = tmp_path / "logs"
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=RunLog(logs_dir))
    metrics.register_rows()
    pool_collector = _CollectingPoolCollector()
    start = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    reset_time = start + timedelta(minutes=5)
    clock = _FakeClock(start)
    quota_wall = _quota.QuotaWall(now_fn=clock.now, sleep_fn=clock.sleep)
    llm_enricher = _UsageLimitThenMatchedEnricher(reset_time=reset_time)

    class _FakeDateTime:
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            now = clock.now()
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    monkeypatch.setattr("application_pipeline.classify_stage.datetime", _FakeDateTime)

    stage = ClassifyStage(
        batch_size=1,
        parallelism=1,
        pool_collector=pool_collector,
        llm_enricher=llm_enricher,
        metrics=metrics,
        run_state=_FakeRunState(),
        run_log=RunLog(logs_dir),
        quota_wall=quota_wall,
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    _submit_ready(handoff, 1)
    stage.close()
    completion = stage.wait()

    assert completion.first_failure is None
    assert llm_enricher.calls == 2
    assert clock.sleep_calls == [420.0]
    assert metrics.classify_calls == 1
    assert _last_body(display, "llm classify relevance") == "1 forwarded"
    assert pool_collector.matched == [(1, _classify_request(1).submission.stub)]
    assert [row["event"] for row in _pipeline_event_rows(logs_dir)] == ["quota_sleep"]
    classify_rows = _classify_event_rows(logs_dir)
    assert classify_rows == [
        {
            "ts": classify_rows[0]["ts"],
            "event": "classify_relevance",
            "matches": True,
        }
    ]


def test_classify_stage_parallel_workers_log_one_quota_sleep_and_wait_for_active_wall(
    tmp_path: Path, monkeypatch
) -> None:
    logs_dir = tmp_path / "logs"
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=RunLog(logs_dir))
    metrics.register_rows()
    pool_collector = _CollectingPoolCollector()
    start = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    reset_time = start + timedelta(minutes=5)
    clock = _ControllableSleepClock(start)
    quota_wall = _quota.QuotaWall(now_fn=clock.now, sleep_fn=clock.sleep)
    llm_enricher = _ParallelQuotaWallEnricher(reset_time=reset_time)

    class _FakeDateTime:
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            now = clock.now()
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    monkeypatch.setattr("application_pipeline.classify_stage.datetime", _FakeDateTime)

    stage = ClassifyStage(
        batch_size=1,
        parallelism=2,
        pool_collector=pool_collector,
        llm_enricher=llm_enricher,
        metrics=metrics,
        run_state=_FakeRunState(),
        run_log=RunLog(logs_dir),
        quota_wall=quota_wall,
    )
    handoff = stage.handoff_for(parser_id="parser.test", metrics=metrics)

    stage.start()
    _submit_ready(handoff, 1)
    _submit_ready(handoff, 2)
    _submit_ready(handoff, 3)
    stage.close()

    assert llm_enricher.wall_raised.wait(timeout=1)
    llm_enricher.release_second_batch.set()
    assert clock.sleep_started.wait(timeout=1)
    assert llm_enricher.third_batch_started.is_set() is False

    clock.allow_sleep.set()
    completion = stage.wait()

    assert completion.first_failure is None
    assert metrics.classify_calls == 3
    assert sorted(pool_collector.matched, key=lambda item: item[0]) == [
        (1, _classify_request(1).submission.stub),
        (2, _classify_request(2).submission.stub),
        (3, _classify_request(3).submission.stub),
    ]
    assert [row["event"] for row in _pipeline_event_rows(logs_dir)] == ["quota_sleep"]
