from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

import httpx

from application_pipeline.classify_stage import ClassifyStageHandoff
from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import RunScopedSeenKind, RunScopedSeenResult
from application_pipeline.extracts.card_store import CardStore
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import Parser, PositionStub
from application_pipeline.parsers.body_fetch import OversizedBodyError
from application_pipeline.parsers.types import EnrichFailedError
from application_pipeline.run_metrics import (
    ParserIntakeDropObservation,
    ParserIntakeEnrichFailureObservation,
    ParserIntakeForwardedObservation,
)


@runtime_checkable
class Deduplication(Protocol):
    def is_seen(self, key: PositionStub) -> RunScopedSeenResult: ...


@runtime_checkable
class DomainPreFilter(Protocol):
    def admit(self, stub: PositionStub) -> bool: ...


@runtime_checkable
class DedupEventRecorder(Protocol):
    def record(self, result: RunScopedSeenKind) -> None: ...


@runtime_checkable
class PoolCollector(Protocol):
    def add_judge_pending(self, stub: PositionStub, listing_id: int) -> None: ...


class _NullPoolCollector:
    def add_judge_pending(self, stub: PositionStub, listing_id: int) -> None:
        pass


class _NullClassifyStageHandoff:
    def submit_ready(
        self,
        *,
        listing_id: int,
        stub: PositionStub,
        raw_description: str,
        parser_id: str,
    ) -> None:
        pass


@runtime_checkable
class ParserMetrics(Protocol):
    def observe_parser_intake_drop(
        self, observation: ParserIntakeDropObservation
    ) -> None: ...

    def observe_parser_intake_enrich_failure(
        self, observation: ParserIntakeEnrichFailureObservation
    ) -> None: ...

    def observe_parser_intake_forwarded(
        self, observation: ParserIntakeForwardedObservation
    ) -> None: ...


DropReason = Literal[
    "freshness_discover",
    "dedup_url_hit",
    "dedup_tuple_hit",
    "dedup_fuzzy_hit",
    "dedup_run_hit",
    "prefilter",
    "freshness_post_enrich",
    "content_empty_body",
    "content_too_short",
]


class ParserIntake:
    def __init__(
        self,
        *,
        parser_id: str = "",
        parser: Parser,
        freshness_gate: FreshnessGate,
        deduplication: Deduplication,
        dedup_counters: DedupEventRecorder,
        domain_pre_filter: DomainPreFilter,
        content_gate: ContentGate,
        card_store: CardStore,
        pool_collector: PoolCollector | None = None,
        classify_handoff: ClassifyStageHandoff | None = None,
        run_log: RunLog,
        metrics: ParserMetrics | None = None,
    ) -> None:
        self._parser_id = parser_id
        self._parser = parser
        self._freshness_gate = freshness_gate
        self._deduplication = deduplication
        self._dedup_counters = dedup_counters
        self._domain_pre_filter = domain_pre_filter
        self._content_gate = content_gate
        self._card_store = card_store
        self._pool_collector = (
            _NullPoolCollector() if pool_collector is None else pool_collector
        )
        self._classify_handoff = (
            _NullClassifyStageHandoff()
            if classify_handoff is None
            else classify_handoff
        )
        self._run_log = run_log
        self._metrics = metrics

    def process_position_stub(self, position_stub: PositionStub) -> None:
        if not self._freshness_gate.admit(
            position_stub,
            gate_arm="discover",
            deadline=position_stub.deadline,
        ):
            self._observe_drop_metric("freshness_discover")
            return

        discover_dedup = self._deduplication.is_seen(position_stub)
        if discover_dedup.kind == "judge_pending":
            self._admit_judge_pending(
                listing_id=discover_dedup.listing_id,
                stub=position_stub,
            )
            self._record_dedup("judge_pending")
            return
        if discover_dedup.kind != "miss":
            self._record_dedup(discover_dedup.kind)
            self._observe_drop_metric(_drop_reason_for_dedup(discover_dedup.kind))
            return

        if not self._domain_pre_filter.admit(position_stub):
            self._record_dedup("miss")
            self._observe_drop_metric("prefilter")
            return

        try:
            enrich_result = self._parser.enrich(position_stub)
        except EnrichFailedError:
            self._record_dedup("miss")
            self._run_log.event(
                "pipeline_orchestrator",
                "enrich_failed",
                url=position_stub.url,
                source=position_stub.source,
            )
            self._observe_enrich_failed_metric()
            return
        except OversizedBodyError as exc:
            self._record_dedup("miss")
            self._run_log.event(
                "llm_enricher",
                "body_oversized",
                url=exc.url,
                source=exc.source,
                body_len=exc.body_len,
            )
            return
        except httpx.HTTPError as exc:
            self._record_dedup("miss")
            self._run_log.event(
                "llm_enricher",
                "fetch_transient_error",
                url=position_stub.url,
                source=position_stub.source,
                error=str(exc),
            )
            return

        stub = enrich_result.stub
        body = enrich_result.body

        post_enrich_dedup = self._deduplication.is_seen(stub)
        if post_enrich_dedup.kind not in ("miss", "run_hit", "judge_pending"):
            self._record_dedup(post_enrich_dedup.kind)
            self._observe_drop_metric(_drop_reason_for_dedup(post_enrich_dedup.kind))
            return

        if not self._freshness_gate.admit(
            stub,
            gate_arm="post_enrich",
            deadline=stub.deadline,
        ):
            self._record_dedup(post_enrich_dedup.kind)
            self._observe_drop_metric("freshness_post_enrich")
            return

        content_decision = self._content_gate.inspect(body, stub)
        if not content_decision.passes:
            self._record_dedup(post_enrich_dedup.kind)
            self._observe_drop_metric(_drop_reason_for_content(content_decision.reason))
            return

        if post_enrich_dedup.kind == "judge_pending":
            self._refresh_card_store_body(
                listing_id=post_enrich_dedup.listing_id,
                body=body,
            )
            self._admit_judge_pending(
                listing_id=post_enrich_dedup.listing_id,
                stub=stub,
            )
            self._record_dedup("judge_pending")
            return

        self._record_dedup(post_enrich_dedup.kind)
        self._observe_forwarded_metric(enrich_result.mode)
        self._classify_handoff.submit_ready(
            listing_id=post_enrich_dedup.listing_id,
            stub=stub,
            raw_description=body,
            parser_id=self._parser_id,
        )
        return

    def _refresh_card_store_body(self, *, listing_id: int, body: str) -> None:
        self._card_store.replace_body_if_present(listing_id, body)

    def _admit_judge_pending(self, *, listing_id: int, stub: PositionStub) -> None:
        self._pool_collector.add_judge_pending(stub, listing_id)

    def _record_dedup(self, kind: RunScopedSeenKind) -> None:
        self._dedup_counters.record(kind)

    def _observe_drop_metric(self, reason: DropReason) -> None:
        if self._metrics is None or not self._parser_id:
            return
        self._metrics.observe_parser_intake_drop(
            ParserIntakeDropObservation(parser_id=self._parser_id, outcome=reason)
        )

    def _observe_enrich_failed_metric(self) -> None:
        if self._metrics is None or not self._parser_id:
            return
        self._metrics.observe_parser_intake_enrich_failure(
            ParserIntakeEnrichFailureObservation(parser_id=self._parser_id)
        )

    def _observe_forwarded_metric(self, mode: Literal["native", "fallback"]) -> None:
        if self._metrics is None or not self._parser_id:
            return
        self._metrics.observe_parser_intake_forwarded(
            ParserIntakeForwardedObservation(parser_id=self._parser_id, mode=mode)
        )


def _drop_reason_for_dedup(kind: RunScopedSeenKind) -> DropReason:
    if kind == "url_hit":
        return "dedup_url_hit"
    if kind == "tuple_hit":
        return "dedup_tuple_hit"
    if kind == "fuzzy_hit":
        return "dedup_fuzzy_hit"
    if kind == "run_hit":
        return "dedup_run_hit"
    raise ValueError(f"unsupported dedup drop kind: {kind}")


def _drop_reason_for_content(
    reason: Literal["passed", "empty_body", "too_short"],
) -> DropReason:
    if reason == "empty_body":
        return "content_empty_body"
    if reason == "too_short":
        return "content_too_short"
    raise ValueError(f"unsupported content drop reason: {reason}")
