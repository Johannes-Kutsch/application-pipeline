from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

import httpx

from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import RunScopedSeenKind, RunScopedSeenResult
from application_pipeline.extracts.card_store import CardExtract, CardStore
from application_pipeline.freshness_gate import FreshnessGate
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
class ParserLogArtifact:
    component: str
    event: str
    fields: dict[str, object]


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


DropReason = Literal[
    "freshness_discover",
    "dedup_url_hit",
    "dedup_tuple_hit",
    "dedup_fuzzy_hit",
    "dedup_run_hit",
    "prefilter",
    "freshness_post_enrich",
    "content",
]


@dataclass(frozen=True)
class ClassifyForwarded:
    stub: PositionStub
    listing_id: ListingId
    body: str
    enrich_mode: Literal["native", "fallback"]
    dedup_events: tuple[RunScopedSeenKind, ...] = ("miss",)

    @property
    def parser_row_metric(self) -> ParserRowMetric:
        return "forwarded"

    @property
    def parser_log_artifact(self) -> ParserLogArtifact | None:
        return None


@dataclass(frozen=True)
class PoolAdmitted:
    pool_admission: PoolAdmission
    dedup_kind: Literal["judge_pending"]
    dedup_events: tuple[RunScopedSeenKind, ...] = ("judge_pending",)

    @property
    def parser_row_metric(self) -> ParserRowMetric | None:
        return None

    @property
    def parser_log_artifact(self) -> ParserLogArtifact | None:
        return None


@dataclass(frozen=True)
class Dropped:
    reason: DropReason
    stub: PositionStub
    listing_id: ListingId | None = None
    dedup_kind: RunScopedSeenKind | None = None
    dedup_events: tuple[RunScopedSeenKind, ...] = ()

    @property
    def parser_row_metric(self) -> ParserRowMetric:
        if self.reason.startswith("freshness_"):
            return "freshness_dropped"
        if self.reason.startswith("dedup_"):
            return "dedup_dropped"
        if self.reason == "prefilter":
            return "prefilter_dropped"
        return "content_dropped"

    @property
    def parser_log_artifact(self) -> ParserLogArtifact | None:
        return None


@dataclass(frozen=True)
class RetryableEnrichFailure:
    stub: PositionStub
    error: EnrichFailedError
    dedup_events: tuple[RunScopedSeenKind, ...] = ("miss",)

    @property
    def parser_row_metric(self) -> ParserRowMetric:
        return "enrich_failed"

    @property
    def parser_log_artifact(self) -> ParserLogArtifact:
        return ParserLogArtifact(
            component="pipeline_orchestrator",
            event="enrich_failed",
            fields={
                "url": self.stub.url,
                "source": self.stub.source,
            },
        )


@dataclass(frozen=True)
class OversizedBodySkip:
    stub: PositionStub
    error: OversizedBodyError
    dedup_events: tuple[RunScopedSeenKind, ...] = ("miss",)

    @property
    def parser_row_metric(self) -> ParserRowMetric | None:
        return None

    @property
    def parser_log_artifact(self) -> ParserLogArtifact:
        return ParserLogArtifact(
            component="llm_enricher",
            event="body_oversized",
            fields={
                "url": self.error.url,
                "source": self.error.source,
                "body_len": self.error.body_len,
            },
        )


@dataclass(frozen=True)
class TransientHttpSkip:
    stub: PositionStub
    error: httpx.HTTPError
    dedup_events: tuple[RunScopedSeenKind, ...] = ("miss",)

    @property
    def parser_row_metric(self) -> ParserRowMetric | None:
        return None

    @property
    def parser_log_artifact(self) -> ParserLogArtifact:
        return ParserLogArtifact(
            component="llm_enricher",
            event="fetch_transient_error",
            fields={
                "url": self.stub.url,
                "source": self.stub.source,
                "error": str(self.error),
            },
        )


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
        parser: Parser,
        freshness_gate: FreshnessGate,
        deduplication: Deduplication,
        domain_pre_filter: DomainPreFilter,
        content_gate: ContentGate,
        card_store: CardStore,
    ) -> None:
        self._parser = parser
        self._freshness_gate = freshness_gate
        self._deduplication = deduplication
        self._domain_pre_filter = domain_pre_filter
        self._content_gate = content_gate
        self._card_store = card_store

    def process_position_stub(self, position_stub: PositionStub) -> ParserIntakeOutcome:
        if not self._freshness_gate.admit(
            position_stub,
            gate_arm="discover",
            deadline=position_stub.deadline,
        ):
            return Dropped(reason="freshness_discover", stub=position_stub)

        discover_dedup = self._deduplication.is_seen(position_stub)
        if discover_dedup.kind == "judge_pending":
            return PoolAdmitted(
                pool_admission=PoolAdmission(
                    listing_id=discover_dedup.listing_id,
                    stub=position_stub,
                ),
                dedup_kind="judge_pending",
                dedup_events=("judge_pending",),
            )
        if discover_dedup.kind != "miss":
            return Dropped(
                reason=_drop_reason_for_dedup(discover_dedup.kind),
                stub=position_stub,
                listing_id=discover_dedup.listing_id,
                dedup_kind=discover_dedup.kind,
                dedup_events=(discover_dedup.kind,),
            )

        if not self._domain_pre_filter.admit(position_stub):
            return Dropped(
                reason="prefilter",
                stub=position_stub,
                listing_id=discover_dedup.listing_id,
                dedup_events=("miss",),
            )

        try:
            enrich_result = self._parser.enrich(position_stub)
        except EnrichFailedError as exc:
            return RetryableEnrichFailure(
                stub=position_stub,
                error=exc,
                dedup_events=("miss",),
            )
        except OversizedBodyError as exc:
            return OversizedBodySkip(
                stub=position_stub,
                error=exc,
                dedup_events=("miss",),
            )
        except httpx.HTTPError as exc:
            return TransientHttpSkip(
                stub=position_stub,
                error=exc,
                dedup_events=("miss",),
            )

        stub = enrich_result.stub
        body = enrich_result.body

        post_enrich_dedup = self._deduplication.is_seen(stub)
        if post_enrich_dedup.kind == "judge_pending":
            self._refresh_card_store_body(
                listing_id=post_enrich_dedup.listing_id,
                body=body,
            )
            return PoolAdmitted(
                pool_admission=PoolAdmission(
                    listing_id=post_enrich_dedup.listing_id,
                    stub=stub,
                ),
                dedup_kind="judge_pending",
                dedup_events=("miss", "judge_pending"),
            )
        if post_enrich_dedup.kind == "tuple_hit":
            return Dropped(
                reason=_drop_reason_for_dedup(post_enrich_dedup.kind),
                stub=stub,
                listing_id=post_enrich_dedup.listing_id,
                dedup_kind=post_enrich_dedup.kind,
                dedup_events=("miss", "tuple_hit"),
            )

        if not self._freshness_gate.admit(
            stub,
            gate_arm="post_enrich",
            deadline=stub.deadline,
        ):
            return Dropped(
                reason="freshness_post_enrich",
                stub=stub,
                dedup_events=("miss",),
            )

        if not self._content_gate.admit(body, stub):
            return Dropped(reason="content", stub=stub, dedup_events=("miss",))

        return ClassifyForwarded(
            stub=stub,
            listing_id=post_enrich_dedup.listing_id,
            body=body,
            enrich_mode=enrich_result.mode,
            dedup_events=("miss",),
        )

    def _refresh_card_store_body(self, *, listing_id: ListingId, body: str) -> None:
        existing = self._card_store.get(listing_id)
        if existing is None or not body:
            return
        self._card_store.put(
            listing_id,
            CardExtract(
                header=existing.header,
                summary=existing.summary,
                body=body,
            ),
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
