from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from application_pipeline._context import current_stage
from application_pipeline.llm import ExtractorBatchMalformedError, ExtractorError
from application_pipeline.llm import ClaudeUsageLimitError
from application_pipeline.llm import quota as _quota
from application_pipeline.llm.types import AppliedClassifyOutcome
from application_pipeline.parser_log import RunLog

from application_pipeline.parsers.types import PositionStub

ListingId = int
RawDescription = str
ParserIdentity = str

_log = logging.getLogger("application_pipeline.orchestrator")

__all__ = [
    "BatchLLMEnricher",
    "ClassifyPoolCollector",
    "ClassifyStage",
    "ClassifyStageCompletion",
    "ClassifyStageHandoff",
    "ClassifyStageMetrics",
    "ClassifyStageRunState",
    "assert_classify_stage_ownership",
]


@dataclass(frozen=True)
class _ClassifyReadySubmission:
    listing_id: ListingId
    stub: PositionStub
    raw_description: RawDescription


@dataclass(frozen=True)
class _ClassifyRequest:
    submission: _ClassifyReadySubmission
    parser_id: ParserIdentity


@runtime_checkable
class ClassifyStageHandoff(Protocol):
    def submit_ready(
        self,
        *,
        listing_id: ListingId,
        stub: PositionStub,
        raw_description: RawDescription,
        parser_id: ParserIdentity,
    ) -> None: ...


class _ClassifyShutdown:
    __slots__ = ()


_CLASSIFY_SHUTDOWN = _ClassifyShutdown()
_ClassifyBatch = list[_ClassifyRequest]
_ClassifyQueueItem = _ClassifyRequest | _ClassifyShutdown
_ClassifyDispatchItem = _ClassifyBatch | _ClassifyShutdown


@dataclass(frozen=True)
class _ClaimedDispatchItem:
    item: _ClassifyDispatchItem
    from_retry: bool


@runtime_checkable
class ClassifyAccumulatorMetrics(Protocol):
    def classify_batch_started(self, count: int) -> None: ...


@runtime_checkable
class ClassifyAccumulatorRunState(Protocol):
    def set_aborted(self, exc: BaseException) -> None: ...


@runtime_checkable
class ClassifyWorkerRunState(Protocol):
    @property
    def is_degraded(self) -> bool: ...

    def set_aborted(self, exc: BaseException) -> None: ...


@runtime_checkable
class ClassifyWorkerMetrics(Protocol):
    def classify_batch_failed(self, items: int) -> None: ...

    def classify_batch_succeeded(
        self,
        outcome: AppliedClassifyOutcome,
        *,
        parser_ids: tuple[str, ...] = (),
    ) -> None: ...


@runtime_checkable
class ClassifyPoolCollector(Protocol):
    def add_matched(self, stub: PositionStub, listing_id: int) -> None: ...


@runtime_checkable
class BatchLLMEnricher(Protocol):
    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome: ...


class _QueueBackedClassifyStageHandoff(ClassifyStageHandoff):
    def __init__(
        self,
        *,
        classify_queue: queue.Queue[_ClassifyQueueItem],
        metrics: "ClassifyStageMetrics",
        parser_id: str,
    ) -> None:
        self._classify_queue = classify_queue
        self._metrics = metrics
        self._parser_id = parser_id

    def _submit_request(self, request: _ClassifyRequest) -> None:
        assert request.parser_id == self._parser_id
        self._classify_queue.put(request)
        self._metrics.classify_submitted(1)

    def submit_ready(
        self,
        *,
        listing_id: ListingId,
        stub: PositionStub,
        raw_description: RawDescription,
        parser_id: ParserIdentity,
    ) -> None:
        self._submit_request(
            _ClassifyRequest(
                submission=_ClassifyReadySubmission(
                    listing_id=listing_id,
                    stub=stub,
                    raw_description=raw_description,
                ),
                parser_id=parser_id,
            )
        )


@runtime_checkable
class ClassifyStageMetrics(ClassifyAccumulatorMetrics, ClassifyWorkerMetrics, Protocol):
    def classify_submitted(self, count: int) -> None: ...

    def classify_stage_completed(self) -> None: ...


@runtime_checkable
class ClassifyStageRunState(
    ClassifyAccumulatorRunState, ClassifyWorkerRunState, Protocol
):
    pass


@dataclass(frozen=True)
class ClassifyStageCompletion:
    first_failure: BaseException | None


class ClassifyStage:
    def __init__(
        self,
        *,
        batch_size: int,
        parallelism: int,
        pool_collector: ClassifyPoolCollector,
        llm_enricher: BatchLLMEnricher,
        metrics: ClassifyStageMetrics,
        run_state: ClassifyStageRunState,
        run_log: RunLog,
        quota_wall: _quota.QuotaWall,
    ) -> None:
        self._classify_queue: queue.Queue[_ClassifyQueueItem] = queue.Queue()
        self._metrics = metrics
        self._dispatch = _QuotaCoordinatedDispatch(quota_wall=quota_wall)
        self._accumulator = _ClassifyAccumulator(
            classify_queue=self._classify_queue,
            dispatch=self._dispatch,
            batch_size=batch_size,
            num_workers=parallelism,
            metrics=metrics,
            run_state=run_state,
        )
        self._workers = [
            _ClassifyWorker(
                dispatch=self._dispatch,
                pool_collector=pool_collector,
                llm_enricher=llm_enricher,
                metrics=metrics,
                run_state=run_state,
                run_log=run_log,
                worker_index=i,
            )
            for i in range(parallelism)
        ]
        self._closed = False

    def start(self) -> None:
        self._accumulator.start()
        for worker in self._workers:
            worker.start()

    def handoff_for(
        self, *, parser_id: str, metrics: ClassifyStageMetrics
    ) -> ClassifyStageHandoff:
        return _QueueBackedClassifyStageHandoff(
            classify_queue=self._classify_queue,
            metrics=metrics,
            parser_id=parser_id,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._classify_queue.put(_CLASSIFY_SHUTDOWN)

    def wait(self) -> ClassifyStageCompletion:
        self._accumulator.join()
        first_failure = self._accumulator.exc
        for worker in self._workers:
            worker.join()
            if first_failure is None and worker.exc is not None:
                first_failure = worker.exc
        self._metrics.classify_stage_completed()
        return ClassifyStageCompletion(first_failure=first_failure)


class _ClassifyAccumulator(threading.Thread):
    """Single thread that fills classify batches sequentially."""

    def __init__(
        self,
        *,
        classify_queue: queue.Queue[_ClassifyQueueItem],
        dispatch: "_QuotaCoordinatedDispatch",
        batch_size: int,
        num_workers: int,
        metrics: ClassifyAccumulatorMetrics,
        run_state: ClassifyAccumulatorRunState,
    ) -> None:
        super().__init__(name="classify-accumulator", daemon=True)
        self._classify_queue = classify_queue
        self._dispatch = dispatch
        self._batch_size = batch_size
        self._num_workers = num_workers
        self._metrics = metrics
        self._run_state = run_state
        self.exc: BaseException | None = None

    def run(self) -> None:
        current_stage.set("classify-accumulator")
        try:
            batch: _ClassifyBatch = []
            while True:
                item = self._classify_queue.get()
                if item is _CLASSIFY_SHUTDOWN:
                    if batch:
                        self._metrics.classify_batch_started(len(batch))
                        self._dispatch.submit(batch)
                    for _ in range(self._num_workers):
                        self._dispatch.submit(_CLASSIFY_SHUTDOWN)
                    break
                assert isinstance(item, _ClassifyRequest)
                batch.append(item)
                if len(batch) >= self._batch_size:
                    self._metrics.classify_batch_started(len(batch))
                    self._dispatch.submit(batch)
                    batch = []
        except BaseException as exc:
            self.exc = exc
            self._run_state.set_aborted(exc)
            for _ in range(self._num_workers):
                self._dispatch.submit(_CLASSIFY_SHUTDOWN)


class _QuotaCoordinatedDispatch:
    def __init__(self, *, quota_wall: _quota.QuotaWall) -> None:
        self._quota_wall = quota_wall
        self._cond = threading.Condition()
        self._ready: deque[_ClassifyDispatchItem] = deque()
        self._retry: deque[_ClassifyBatch] = deque()
        self._wall_generation = 0
        self._retry_inflight = False

    def submit(self, item: _ClassifyDispatchItem) -> None:
        with self._cond:
            self._ready.append(item)
            self._cond.notify_all()

    def retry(self, batch: _ClassifyBatch, *, from_retry: bool) -> None:
        with self._cond:
            if from_retry:
                self._retry_inflight = False
            self._retry.append(batch)
            self._cond.notify_all()

    def finish_retry(self) -> None:
        with self._cond:
            self._retry_inflight = False
            self._cond.notify_all()

    def raise_wall(self, reset_time: datetime) -> bool:
        with self._cond:
            is_first = self._quota_wall.raise_wall(reset_time)
            if is_first:
                self._wall_generation += 1
            self._cond.notify_all()
            return is_first

    def claim(self) -> _ClaimedDispatchItem:
        while True:
            self._quota_wall.wait_if_blocked()
            with self._cond:
                while not self._retry and not self._ready:
                    self._cond.wait()
                if self._quota_wall.is_active():
                    continue
                if self._retry:
                    self._retry_inflight = True
                    return _ClaimedDispatchItem(
                        item=self._retry.popleft(),
                        from_retry=True,
                    )
                if self._retry_inflight:
                    self._cond.wait()
                    continue
                generation = self._wall_generation
                item = self._ready.popleft()
                if self._wall_generation != generation or self._quota_wall.is_active():
                    self._ready.appendleft(item)
                    continue
                return _ClaimedDispatchItem(item=item, from_retry=False)


class _ClassifyWorker(threading.Thread):
    def __init__(
        self,
        *,
        dispatch: _QuotaCoordinatedDispatch,
        pool_collector: ClassifyPoolCollector,
        llm_enricher: BatchLLMEnricher,
        metrics: ClassifyWorkerMetrics,
        run_state: ClassifyWorkerRunState,
        run_log: RunLog,
        worker_index: int = 0,
    ) -> None:
        super().__init__(name=f"classify-worker-{worker_index}", daemon=True)
        self._dispatch = dispatch
        self._pool_collector = pool_collector
        self._llm_enricher = llm_enricher
        self._metrics = metrics
        self._run_state = run_state
        self._run_log = run_log
        self.exc: BaseException | None = None

    def run(self) -> None:
        current_stage.set("classify")
        claimed: _ClaimedDispatchItem | None = None
        try:
            while True:
                claimed = self._dispatch.claim()
                item = claimed.item
                if item is _CLASSIFY_SHUTDOWN:
                    break
                assert isinstance(item, list)
                batch: list[_ClassifyRequest] = item
                if not self._run_state.is_degraded:
                    self._process_batch(batch, from_retry=claimed.from_retry)
                elif claimed.from_retry:
                    self._dispatch.finish_retry()
                claimed = None
        except BaseException as exc:
            if claimed is not None and claimed.from_retry:
                self._dispatch.finish_retry()
            self.exc = exc
            self._run_state.set_aborted(exc)

    def _process_batch(
        self, batch: list[_ClassifyRequest], *, from_retry: bool
    ) -> None:
        items = [
            (
                req.submission.listing_id,
                req.submission.stub,
                req.submission.raw_description,
            )
            for req in batch
        ]

        try:
            outcome = self._llm_enricher.enrich(items)
        except ClaudeUsageLimitError as err:
            self._dispatch.retry(batch, from_retry=from_retry)
            self._raise_quota_wall(err)
            return
        except ExtractorBatchMalformedError as exc:
            self._handle_failed_batch(batch, exc, from_retry=from_retry)
            return
        except ExtractorError as exc:
            self._handle_failed_batch(batch, exc, from_retry=from_retry)
            return

        self._apply_outcome(batch, outcome)
        if from_retry:
            self._dispatch.finish_retry()

    def _handle_failed_batch(
        self,
        batch: list[_ClassifyRequest],
        exc: ExtractorError,
        *,
        from_retry: bool,
    ) -> None:
        if from_retry:
            self._dispatch.finish_retry()
        _log.warning("llm_enricher.enrich failed: %s", exc)
        self._metrics.classify_batch_failed(len(batch))
        self._run_log.event(
            "llm_classify_relevance",
            "classify_relevance",
            status="error",
            error=str(exc),
        )

    def _raise_quota_wall(self, err: ClaudeUsageLimitError) -> None:
        now = datetime.now(timezone.utc)
        wake = _quota.compute_wake_time(err.reset_time, now)
        duration_s = max(0.0, (wake - now).total_seconds())
        is_first = self._dispatch.raise_wall(wake - _quota._BUFFER)
        if is_first:
            self._run_log.event(
                "pipeline_orchestrator",
                "quota_sleep",
                reset_time=err.reset_time.isoformat()
                if err.reset_time is not None
                else None,
                wake_time=wake.isoformat(),
                duration_s=duration_s,
            )

    def _apply_outcome(
        self, batch: list[_ClassifyRequest], outcome: AppliedClassifyOutcome
    ) -> None:
        matched_submissions: list[tuple[int, PositionStub]] = []
        for req, item_outcome in zip(batch, outcome.items):
            self._run_log.event(
                "llm_classify_relevance",
                "classify_relevance",
                matches=item_outcome.event_matches,
            )
            if item_outcome.matched_listing is not None:
                matched_submissions.append(
                    (req.submission.listing_id, req.submission.stub)
                )

        for listing_id, stub in matched_submissions:
            self._pool_collector.add_matched(stub, listing_id)

        self._metrics.classify_batch_succeeded(
            outcome,
            parser_ids=tuple(req.parser_id for req in batch),
        )


def assert_classify_stage_ownership() -> None:
    owned_symbols = {
        "_ClassifyReadySubmission": _ClassifyReadySubmission,
        "_ClassifyRequest": _ClassifyRequest,
        "_ClassifyShutdown": _ClassifyShutdown,
        "_CLASSIFY_SHUTDOWN": _CLASSIFY_SHUTDOWN.__class__,
        "_ClassifyAccumulator": _ClassifyAccumulator,
        "_ClassifyWorker": _ClassifyWorker,
        "_QueueBackedClassifyStageHandoff": _QueueBackedClassifyStageHandoff,
        "ClassifyStageMetrics": ClassifyStageMetrics,
        "ClassifyPoolCollector": ClassifyPoolCollector,
    }
    for name, symbol in owned_symbols.items():
        owner = getattr(symbol, "__module__", None)
        if owner != __name__:
            raise AssertionError(
                f"{name} must stay owned by {__name__}, found {owner!r}"
            )


assert_classify_stage_ownership()
