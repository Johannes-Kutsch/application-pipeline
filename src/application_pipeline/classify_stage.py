from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from application_pipeline._context import current_stage

from application_pipeline.parsers.types import PositionStub

ListingId = int
RawDescription = str
ParserIdentity = str


@dataclass(frozen=True)
class ClassifyReadySubmission:
    listing_id: ListingId
    stub: PositionStub
    raw_description: RawDescription


@dataclass(frozen=True)
class ClassifyRequest:
    submission: ClassifyReadySubmission
    parser_id: ParserIdentity


@runtime_checkable
class ClassifyStageHandoff(Protocol):
    def submit(self, request: ClassifyRequest) -> None: ...


class ClassifyShutdown:
    __slots__ = ()


CLASSIFY_SHUTDOWN = ClassifyShutdown()
ClassifyBatch = list[ClassifyRequest]
ClassifyQueueItem = ClassifyRequest | ClassifyShutdown
ClassifyDispatchItem = ClassifyBatch | ClassifyShutdown


@runtime_checkable
class ClassifyAccumulatorMetrics(Protocol):
    def classify_batch_dequeued(self, size: int) -> None: ...


@runtime_checkable
class ClassifyAccumulatorRunState(Protocol):
    def set_aborted(self, exc: BaseException) -> None: ...


class ClassifyAccumulator(threading.Thread):
    """Single thread that fills classify batches sequentially."""

    def __init__(
        self,
        *,
        classify_queue: queue.Queue[ClassifyQueueItem],
        dispatch_queue: queue.Queue[ClassifyDispatchItem],
        batch_size: int,
        num_workers: int,
        metrics: ClassifyAccumulatorMetrics,
        run_state: ClassifyAccumulatorRunState,
    ) -> None:
        super().__init__(name="classify-accumulator", daemon=True)
        self._classify_queue = classify_queue
        self._dispatch_queue = dispatch_queue
        self._batch_size = batch_size
        self._num_workers = num_workers
        self._metrics = metrics
        self._run_state = run_state
        self.exc: BaseException | None = None

    def run(self) -> None:
        current_stage.set("classify-accumulator")
        try:
            batch: ClassifyBatch = []
            while True:
                item = self._classify_queue.get()
                if item is CLASSIFY_SHUTDOWN:
                    if batch:
                        self._metrics.classify_batch_dequeued(len(batch))
                        self._dispatch_queue.put(batch)
                    for _ in range(self._num_workers):
                        self._dispatch_queue.put(CLASSIFY_SHUTDOWN)
                    break
                assert isinstance(item, ClassifyRequest)
                batch.append(item)
                if len(batch) >= self._batch_size:
                    self._metrics.classify_batch_dequeued(len(batch))
                    self._dispatch_queue.put(batch)
                    batch = []
        except BaseException as exc:
            self.exc = exc
            self._run_state.set_aborted(exc)
            for _ in range(self._num_workers):
                self._dispatch_queue.put(CLASSIFY_SHUTDOWN)
