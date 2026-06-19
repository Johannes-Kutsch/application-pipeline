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
    def observe_parser_intake_freshness_drop(
        self, parser_id: str, gate_arm: Literal["discover", "post_enrich"]
    ) -> None: ...

    def observe_parser_intake_dedup_drop(
        self,
        parser_id: str,
        kind: Literal["url_hit", "tuple_hit", "fuzzy_hit", "run_hit"],
    ) -> None: ...

    def observe_parser_intake_prefilter_drop(self, parser_id: str) -> None: ...

    def observe_parser_intake_content_drop(
        self, parser_id: str, reason: Literal["empty_body", "too_short"]
    ) -> None: ...

    def observe_parser_intake_enrich_failure(self, parser_id: str) -> None: ...

    def observe_parser_intake_forwarded(
        self, parser_id: str, mode: Literal["native", "fallback"]
    ) -> None: ...


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
        self._pool_collector = pool_collector or _NullPoolCollector()
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
            self._observe_freshness_drop_metric("discover")
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
            self._observe_dedup_drop_metric(discover_dedup.kind)
            return

        if not self._domain_pre_filter.admit(position_stub):
            self._record_dedup("miss")
            self._observe_prefilter_drop_metric()
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
            self._observe_dedup_drop_metric(post_enrich_dedup.kind)
            return

        if not self._freshness_gate.admit(
            stub,
            gate_arm="post_enrich",
            deadline=stub.deadline,
        ):
            self._record_dedup(post_enrich_dedup.kind)
            self._observe_freshness_drop_metric("post_enrich")
            return

        content_decision = self._content_gate.inspect(body, stub)
        if not content_decision.passes:
            self._record_dedup(post_enrich_dedup.kind)
            self._observe_content_drop_metric(
                _content_drop_reason(content_decision.reason)
            )
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

    def _observe_freshness_drop_metric(
        self, gate_arm: Literal["discover", "post_enrich"]
    ) -> None:
        if self._metrics is None or not self._parser_id:
            return
        self._metrics.observe_parser_intake_freshness_drop(self._parser_id, gate_arm)

    def _observe_dedup_drop_metric(self, kind: RunScopedSeenKind) -> None:
        if self._metrics is None or not self._parser_id:
            return
        if kind not in ("url_hit", "tuple_hit", "fuzzy_hit", "run_hit"):
            raise ValueError(f"unsupported dedup drop kind: {kind}")
        self._metrics.observe_parser_intake_dedup_drop(self._parser_id, kind)

    def _observe_prefilter_drop_metric(self) -> None:
        if self._metrics is None or not self._parser_id:
            return
        self._metrics.observe_parser_intake_prefilter_drop(self._parser_id)

    def _observe_content_drop_metric(
        self, reason: Literal["empty_body", "too_short"]
    ) -> None:
        if self._metrics is None or not self._parser_id:
            return
        self._metrics.observe_parser_intake_content_drop(self._parser_id, reason)

    def _observe_enrich_failed_metric(self) -> None:
        if self._metrics is None or not self._parser_id:
            return
        self._metrics.observe_parser_intake_enrich_failure(self._parser_id)

    def _observe_forwarded_metric(self, mode: Literal["native", "fallback"]) -> None:
        if self._metrics is None or not self._parser_id:
            return
        self._metrics.observe_parser_intake_forwarded(self._parser_id, mode)


def _content_drop_reason(
    reason: Literal["passed", "empty_body", "too_short"],
) -> Literal["empty_body", "too_short"]:
    if reason == "empty_body":
        return "empty_body"
    if reason == "too_short":
        return "too_short"
    raise ValueError(f"unsupported content drop reason: {reason}")
