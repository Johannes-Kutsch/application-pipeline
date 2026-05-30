from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

import httpx

from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import RunScopedSeenKind, RunScopedSeenResult
from application_pipeline.extracts.card_store import CardStore
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import Parser, PositionStub
from application_pipeline.parsers.body_fetch import OversizedBodyError
from application_pipeline.parsers.types import EnrichFailedError

ListingId = int
ParserRowMetric = Literal[
    "content_dropped",
    "dedup_dropped",
    "enrich_failed",
    "forwarded",
    "freshness_dropped",
    "prefilter_dropped",
]


@dataclass(frozen=True)
class PoolAdmission:
    listing_id: ListingId
    stub: PositionStub


@runtime_checkable
class Deduplication(Protocol):
    def is_seen(self, key: PositionStub) -> RunScopedSeenResult: ...


@runtime_checkable
class DomainPreFilter(Protocol):
    def admit(self, stub: PositionStub) -> bool: ...


@runtime_checkable
class DedupEventRecorder(Protocol):
    def record(self, result: RunScopedSeenKind) -> None: ...


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


@dataclass(frozen=True)
class ClassifyForwarded:
    parser_id: str
    stub: PositionStub
    listing_id: ListingId
    body: str
    enrich_mode: Literal["native", "fallback"]
    post_enrich_dedup_kind: RunScopedSeenKind

    @property
    def parser_row_metric(self) -> ParserRowMetric:
        return "forwarded"


@dataclass(frozen=True)
class PoolAdmitted:
    pool_admission: PoolAdmission
    dedup_kind: Literal["judge_pending"]

    @property
    def parser_row_metric(self) -> ParserRowMetric | None:
        return None


@dataclass(frozen=True)
class Dropped:
    reason: DropReason
    stub: PositionStub
    listing_id: ListingId | None = None
    dedup_kind: RunScopedSeenKind | None = None

    @property
    def parser_row_metric(self) -> ParserRowMetric:
        if self.reason.startswith("freshness_"):
            return "freshness_dropped"
        if self.reason.startswith("dedup_"):
            return "dedup_dropped"
        if self.reason == "prefilter":
            return "prefilter_dropped"
        return "content_dropped"


@dataclass(frozen=True)
class RetryableEnrichFailure:
    stub: PositionStub
    error: EnrichFailedError

    @property
    def parser_row_metric(self) -> ParserRowMetric:
        return "enrich_failed"


@dataclass(frozen=True)
class OversizedBodySkip:
    stub: PositionStub
    error: OversizedBodyError

    @property
    def parser_row_metric(self) -> ParserRowMetric | None:
        return None


@dataclass(frozen=True)
class TransientHttpSkip:
    stub: PositionStub
    error: httpx.HTTPError

    @property
    def parser_row_metric(self) -> ParserRowMetric | None:
        return None


ParserIntakeOutcome = (
    ClassifyForwarded
    | PoolAdmitted
    | Dropped
    | RetryableEnrichFailure
    | OversizedBodySkip
    | TransientHttpSkip
)


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
        run_log: RunLog,
    ) -> None:
        self._parser_id = parser_id
        self._parser = parser
        self._freshness_gate = freshness_gate
        self._deduplication = deduplication
        self._dedup_counters = dedup_counters
        self._domain_pre_filter = domain_pre_filter
        self._content_gate = content_gate
        self._card_store = card_store
        self._run_log = run_log

    def process_position_stub(self, position_stub: PositionStub) -> ParserIntakeOutcome:
        if not self._freshness_gate.admit(
            position_stub,
            gate_arm="discover",
            deadline=position_stub.deadline,
        ):
            return Dropped(reason="freshness_discover", stub=position_stub)

        discover_dedup = self._deduplication.is_seen(position_stub)
        if discover_dedup.kind == "judge_pending":
            self._record_dedup("judge_pending")
            return PoolAdmitted(
                pool_admission=PoolAdmission(
                    listing_id=discover_dedup.listing_id,
                    stub=position_stub,
                ),
                dedup_kind="judge_pending",
            )
        if discover_dedup.kind != "miss":
            self._record_dedup(discover_dedup.kind)
            return Dropped(
                reason=_drop_reason_for_dedup(discover_dedup.kind),
                stub=position_stub,
                listing_id=discover_dedup.listing_id,
                dedup_kind=discover_dedup.kind,
            )

        if not self._domain_pre_filter.admit(position_stub):
            self._record_dedup("miss")
            return Dropped(
                reason="prefilter",
                stub=position_stub,
                listing_id=discover_dedup.listing_id,
            )

        try:
            enrich_result = self._parser.enrich(position_stub)
        except EnrichFailedError as exc:
            self._record_dedup("miss")
            self._run_log.event(
                "pipeline_orchestrator",
                "enrich_failed",
                url=position_stub.url,
                source=position_stub.source,
            )
            return RetryableEnrichFailure(
                stub=position_stub,
                error=exc,
            )
        except OversizedBodyError as exc:
            self._record_dedup("miss")
            self._run_log.event(
                "llm_enricher",
                "body_oversized",
                url=exc.url,
                source=exc.source,
                body_len=exc.body_len,
            )
            return OversizedBodySkip(
                stub=position_stub,
                error=exc,
            )
        except httpx.HTTPError as exc:
            self._record_dedup("miss")
            self._run_log.event(
                "llm_enricher",
                "fetch_transient_error",
                url=position_stub.url,
                source=position_stub.source,
                error=str(exc),
            )
            return TransientHttpSkip(
                stub=position_stub,
                error=exc,
            )

        stub = enrich_result.stub
        body = enrich_result.body

        post_enrich_dedup = self._deduplication.is_seen(stub)
        if post_enrich_dedup.kind not in ("miss", "run_hit", "judge_pending"):
            self._record_dedup(post_enrich_dedup.kind)
            return Dropped(
                reason=_drop_reason_for_dedup(post_enrich_dedup.kind),
                stub=stub,
                listing_id=post_enrich_dedup.listing_id,
                dedup_kind=post_enrich_dedup.kind,
            )

        if not self._freshness_gate.admit(
            stub,
            gate_arm="post_enrich",
            deadline=stub.deadline,
        ):
            self._record_dedup(post_enrich_dedup.kind)
            return Dropped(
                reason="freshness_post_enrich",
                stub=stub,
                listing_id=post_enrich_dedup.listing_id,
                dedup_kind=post_enrich_dedup.kind,
            )

        content_decision = self._content_gate.inspect(body, stub)
        if not content_decision.passes:
            self._record_dedup(post_enrich_dedup.kind)
            return Dropped(
                reason=_drop_reason_for_content(content_decision.reason),
                stub=stub,
                listing_id=post_enrich_dedup.listing_id,
                dedup_kind=post_enrich_dedup.kind,
            )

        if post_enrich_dedup.kind == "judge_pending":
            self._refresh_card_store_body(
                listing_id=post_enrich_dedup.listing_id,
                body=body,
            )
            self._record_dedup("judge_pending")
            return PoolAdmitted(
                pool_admission=PoolAdmission(
                    listing_id=post_enrich_dedup.listing_id,
                    stub=stub,
                ),
                dedup_kind="judge_pending",
            )

        self._record_dedup(post_enrich_dedup.kind)
        return ClassifyForwarded(
            parser_id=self._parser_id,
            stub=stub,
            listing_id=post_enrich_dedup.listing_id,
            body=body,
            enrich_mode=enrich_result.mode,
            post_enrich_dedup_kind=post_enrich_dedup.kind,
        )

    def _refresh_card_store_body(self, *, listing_id: ListingId, body: str) -> None:
        self._card_store.replace_body_if_present(listing_id, body)

    def _record_dedup(self, kind: RunScopedSeenKind) -> None:
        self._dedup_counters.record(kind)


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
