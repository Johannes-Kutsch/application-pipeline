from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from application_pipeline._context import current_stage
from application_pipeline.llm import ExtractorError
from application_pipeline.llm.claude_cli import ClaudeUsageLimitError
from application_pipeline.llm import quota as _quota
from application_pipeline.llm.types import AppliedClassifyOutcome, CallUsage
from application_pipeline.parser_log import RunLog

from application_pipeline.parsers.types import PositionStub

ListingId = int
RawDescription = str
ParserIdentity = str

_log = logging.getLogger("application_pipeline.orchestrator")


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


@runtime_checkable
class ClassifyWorkerRunState(Protocol):
    @property
    def is_degraded(self) -> bool: ...

    def set_aborted(self, exc: BaseException) -> None: ...


@runtime_checkable
class ClassifyWorkerMetrics(Protocol):
    def classify_batch_complete(
        self,
        usage: CallUsage,
        items: int,
        classifier_dropped: int,
        retryable_items: int = 0,
    ) -> None: ...

    def classify_batch_failed(self, items: int) -> None: ...

    def enrich_failed(self, parser_id: str = "") -> None: ...


@runtime_checkable
class ClassifyPoolCollector(Protocol):
    def add_matched(self, stub: PositionStub, listing_id: int) -> None: ...


@runtime_checkable
class BatchLLMEnricher(Protocol):
    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome: ...


_ZERO_USAGE = CallUsage(
    input_tokens=0,
    output_tokens=0,
    cache_read_tokens=0,
    cost_usd=0.0,
    duration_s=0.0,
)


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


class ClassifyWorker(threading.Thread):
    def __init__(
        self,
        *,
        dispatch_queue: queue.Queue[ClassifyDispatchItem],
        pool_collector: ClassifyPoolCollector,
        llm_enricher: BatchLLMEnricher,
        metrics: ClassifyWorkerMetrics,
        run_state: ClassifyWorkerRunState,
        run_log: RunLog,
        quota_wall: _quota.QuotaWall,
        worker_index: int = 0,
    ) -> None:
        super().__init__(name=f"classify-worker-{worker_index}", daemon=True)
        self._dispatch_queue = dispatch_queue
        self._pool_collector = pool_collector
        self._llm_enricher = llm_enricher
        self._metrics = metrics
        self._run_state = run_state
        self._run_log = run_log
        self._quota_wall = quota_wall
        self.exc: BaseException | None = None

    def run(self) -> None:
        current_stage.set("classify")
        try:
            while True:
                item = self._dispatch_queue.get()
                if item is CLASSIFY_SHUTDOWN:
                    break
                assert isinstance(item, list)
                batch: list[ClassifyRequest] = item
                if not self._run_state.is_degraded:
                    self._process_batch(batch)
        except BaseException as exc:
            self.exc = exc
            self._run_state.set_aborted(exc)

    def _process_batch(self, batch: list[ClassifyRequest]) -> None:
        items = [
            (
                req.submission.listing_id,
                req.submission.stub,
                req.submission.raw_description,
            )
            for req in batch
        ]

        while True:
            self._quota_wall.wait_if_blocked()
            try:
                outcome = self._llm_enricher.enrich(items)
                break
            except ClaudeUsageLimitError as err:
                self._raise_quota_wall(err)
            except ExtractorError as exc:
                _log.warning("llm_enricher.enrich failed: %s", exc)
                self._metrics.classify_batch_failed(len(batch))
                self._run_log.event(
                    "llm_classify_relevance",
                    "classify_relevance",
                    status="error",
                    error=str(exc),
                )
                return

        self._apply_outcome(batch, outcome)

    def _raise_quota_wall(self, err: ClaudeUsageLimitError) -> None:
        now = datetime.now(timezone.utc)
        wake = _quota.compute_wake_time(err.reset_time, now)
        duration_s = max(0.0, (wake - now).total_seconds())
        is_first = self._quota_wall.raise_wall(wake - _quota._BUFFER)
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
        self, batch: list[ClassifyRequest], outcome: AppliedClassifyOutcome
    ) -> None:
        dropped = 0
        retryable = 0
        for req, item_outcome in zip(batch, outcome.items):
            self._run_log.event(
                "llm_classify_relevance",
                "classify_relevance",
                matches=item_outcome.event_matches,
            )
            if item_outcome.state == "retryable":
                retryable += 1
                self._metrics.enrich_failed(req.submission.stub.source)
                continue
            if item_outcome.state == "expired":
                dropped += 1
                continue
            if item_outcome.state == "rejected":
                dropped += 1
                continue
            if item_outcome.state == "matched":
                matched = item_outcome.matched_listing
                if matched is None or matched.listing_id != req.submission.listing_id:
                    raise AssertionError("matched outcome missing matched listing data")

        for listing_id, stub in outcome.matched_listings:
            self._pool_collector.add_matched(stub, listing_id)

        self._metrics.classify_batch_complete(
            _ZERO_USAGE,
            len(batch),
            dropped,
            retryable_items=retryable,
        )
