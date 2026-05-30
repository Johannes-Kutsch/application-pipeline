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
    CallUsage,
    ClassifyItem,
    ExtractorMalformedError,
    ExtractorMalformedJSONError,
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
    ) -> tuple[list[RelevanceVerdict | None], CallUsage]: ...


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
            raw_verdicts, _ = self._extractor.classify_relevance(classify_items)
        except (ExtractorMalformedError, ExtractorMalformedJSONError) as exc:
            first_stub = items[0][1]
            self._stash_malformed(first_stub, exc)
            self._run_log.event(
                "llm_enricher",
                "classify_malformed",
                url=first_stub.url,
                source=first_stub.source,
                error=str(exc),
            )
            raise

        outcome_items: list[AppliedClassifyItemOutcome] = []
        matched_listings: list[tuple[int, PositionStub]] = []
        for (listing_id, stub, body), verdict in zip(items, raw_verdicts):
            if verdict is None:
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
                matched_listings.append((listing_id, stub))
                outcome_items.append(
                    AppliedClassifyItemOutcome(
                        state="matched",
                        event_matches=True,
                    )
                )
            else:
                if self._dedup_store is not None:
                    self._dedup_store.mark_out_of_domain(listing_id, stub)
                outcome_items.append(
                    AppliedClassifyItemOutcome(
                        state="out_of_domain",
                        event_matches=False,
                    )
                )

        return AppliedClassifyOutcome(
            items=outcome_items,
            matched_listings=matched_listings,
        )

    def _stash_failure(
        self, kind: str, stub: PositionStub, content: str, *, ext: str = "html"
    ) -> None:
        stash_dir = self._failures_dir / kind
        stash_dir.mkdir(parents=True, exist_ok=True)
        slug = stub.url.replace("https://", "").replace("http://", "").replace("/", "-")
        path = stash_dir / f"{stub.source}-{slug}.{ext}"
        path.write_text(content, encoding="utf-8")

    def _stash_malformed(
        self,
        stub: PositionStub,
        exc: ExtractorMalformedError | ExtractorMalformedJSONError,
    ) -> None:
        lines: list[str] = [
            f"**Source:** {stub.source}",
            f"**URL:** {stub.url}",
            f"**Error:** {exc}",
        ]
        if exc.prompt is not None:
            lines += ["", "## Prompt", "", exc.prompt]
        if isinstance(exc, ExtractorMalformedJSONError):
            if exc.stderr:
                lines += ["", "## CLI stderr", "", exc.stderr]
            if exc.returncode is not None:
                lines += ["", f"**Returncode:** {exc.returncode}"]
        elif exc.raw_response is not None:
            lines += ["", "## Raw response", "", exc.raw_response]
        self._stash_failure("malformed", stub, "\n".join(lines), ext="md")
