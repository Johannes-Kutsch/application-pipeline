from __future__ import annotations

import queue

from application_pipeline.classify_stage import (
    CLASSIFY_SHUTDOWN,
    ClassifyAccumulator,
    ClassifyDispatchItem,
    ClassifyQueueItem,
    ClassifyReadySubmission,
    ClassifyRequest,
)
from application_pipeline.parsers import PositionStub


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
