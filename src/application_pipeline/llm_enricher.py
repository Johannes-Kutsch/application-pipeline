"""LLM Enricher - classify + write CardStore."""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from application_pipeline.extracts.card_store import CardExtract, CardStore
from application_pipeline.llm.quota import QuotaWall
from application_pipeline.llm.types import (
    AppliedClassifyItemOutcome,
    AppliedClassifyOutcome,
    ClassifyItem,
    ExtractorBatchMalformedError,
    ExtractorMalformedError,
    ExtractorMalformedJSONError,
    MatchedListing,
    RelevanceVerdict,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.types import PositionStub

if TYPE_CHECKING:
    from application_pipeline.dedup.store import DeduplicationStore
    from application_pipeline.freshness_gate import FreshnessGate


@runtime_checkable
class LLMExtractor(Protocol):
    def classify_relevance(
        self, items: list[ClassifyItem]
    ) -> list[RelevanceVerdict | None]: ...


def _parse_header_date(header: str) -> date | None:
    """Extract posted_date from line 3 of the LLM-authored Header string.

    Header format: title / company·location·work_model / posted_date·seniority·salary
    Returns None when the date segment is absent or unparseable.
    """
    lines = header.split("\n")
    if len(lines) < 3:
        return None
    first_segment = lines[2].split(" · ")[0].strip()
    try:
        return date.fromisoformat(first_segment)
    except ValueError:
        return None


class LLMEnricher:
    """Orchestrate classify -> CardStore write."""

    def __init__(
        self,
        *,
        extractor: LLMExtractor,
        quota_wall: QuotaWall,
        card_store: CardStore,
        run_log: RunLog,
        failures_dir: Path,
        freshness_gate: "FreshnessGate | None" = None,
        dedup_store: "DeduplicationStore | None" = None,
    ) -> None:
        self._extractor = extractor
        self._quota_wall = quota_wall
        self._card_store = card_store
        self._run_log = run_log
        self._failures_dir = failures_dir
        self.freshness_gate: FreshnessGate | None = freshness_gate
        self._dedup_store = dedup_store

    def _last_classify_log_path(self) -> Path | None:
        raw_log_path = getattr(self._extractor, "last_classify_log_path", None)
        return raw_log_path if isinstance(raw_log_path, Path) else None

    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome:
        """Classify a batch, apply local side effects, and return item outcomes."""
        classify_items = [
            ClassifyItem(
                title=stub.title,
                raw_description=body,
                company=stub.company,
                location=stub.location,
                posted_date=stub.posted_date,
            )
            for _, stub, body in items
        ]

        try:
            raw_verdicts = self._extractor.classify_relevance(classify_items)
        except (
            ExtractorBatchMalformedError,
            ExtractorMalformedError,
            ExtractorMalformedJSONError,
        ) as exc:
            agent_runtime_log_path = self._last_classify_log_path()
            # Treat a malformed response as retryable for every item in the batch,
            # stashing one malformed file per listing.
            self._run_log.event(
                "llm_enricher",
                "classify_malformed",
                url=items[0][1].url,
                source=items[0][1].source,
                error=str(exc),
            )
            for _, stub, _ in items:
                self._stash_malformed(stub, exc, agent_runtime_log_path)
            return AppliedClassifyOutcome(
                items=[
                    AppliedClassifyItemOutcome(state="retryable", event_matches=None)
                    for _ in items
                ]
            )

        agent_runtime_log_path = self._last_classify_log_path()

        outcome_items: list[AppliedClassifyItemOutcome] = []
        for (listing_id, stub, body), verdict in zip(items, raw_verdicts):
            if verdict is None:
                self._stash_malformed_listing(stub, agent_runtime_log_path)
                self._run_log.event(
                    "llm_enricher",
                    "classify_malformed",
                    url=stub.url,
                    source=stub.source,
                    error="malformed classifier verdict",
                )
                outcome_items.append(
                    AppliedClassifyItemOutcome(
                        state="retryable",
                        event_matches=None,
                    )
                )
                continue

            if verdict.matches:
                assert verdict.header is not None
                assert verdict.summary is not None

                if self.freshness_gate is not None:
                    updated_stub = dataclasses.replace(
                        stub, posted_date=_parse_header_date(verdict.header)
                    )
                    if not self.freshness_gate.admit(
                        updated_stub, gate_arm="post_llm", deadline=stub.deadline
                    ):
                        outcome_items.append(
                            AppliedClassifyItemOutcome(
                                state="expired",
                                event_matches=None,
                            )
                        )
                        continue

                self._card_store.put(
                    listing_id,
                    CardExtract(
                        header=verdict.header, summary=verdict.summary, body=body
                    ),
                )
                if self._dedup_store is not None:
                    self._dedup_store.mark_matched(listing_id, stub)
                outcome_items.append(
                    AppliedClassifyItemOutcome(
                        state="matched",
                        event_matches=True,
                        matched_listing=MatchedListing(
                            listing_id=listing_id, stub=stub
                        ),
                    )
                )
            else:
                if self._dedup_store is not None:
                    self._dedup_store.mark_out_of_domain(listing_id, stub)
                outcome_items.append(
                    AppliedClassifyItemOutcome(
                        state="rejected",
                        event_matches=False,
                    )
                )

        return AppliedClassifyOutcome(items=outcome_items)

    def _stash_failure(
        self, kind: str, stub: PositionStub, content: str, *, ext: str = "html"
    ) -> None:
        stash_dir = self._failures_dir / kind
        stash_dir.mkdir(parents=True, exist_ok=True)
        slug = stub.url.replace("https://", "").replace("http://", "").replace("/", "-")
        path = stash_dir / f"{stub.source}-{slug}.{ext}"
        path.write_text(content, encoding="utf-8")

    def _stash_malformed_listing(
        self,
        stub: PositionStub,
        agent_runtime_log_path: "Path | None" = None,
    ) -> None:
        lines: list[str] = [
            f"**Source:** {stub.source}",
            f"**URL:** {stub.url}",
            "**Error:** malformed classifier verdict",
        ]
        if agent_runtime_log_path is not None:
            lines += ["", "## Agent Runtime Log", "", str(agent_runtime_log_path)]
        self._stash_failure("malformed", stub, "\n".join(lines), ext="md")

    def _stash_malformed(
        self,
        stub: PositionStub,
        exc: (
            ExtractorBatchMalformedError
            | ExtractorMalformedError
            | ExtractorMalformedJSONError
        ),
        agent_runtime_log_path: "Path | None" = None,
    ) -> None:
        lines: list[str] = [
            f"**Source:** {stub.source}",
            f"**URL:** {stub.url}",
            f"**Error:** {exc}",
        ]
        if agent_runtime_log_path is not None:
            lines += ["", "## Agent Runtime Log", "", str(agent_runtime_log_path)]
        self._stash_failure("malformed", stub, "\n".join(lines), ext="md")
