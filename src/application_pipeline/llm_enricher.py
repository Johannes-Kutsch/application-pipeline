"""LLM Enricher - classify + write CardStore."""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from application_pipeline.extracts.card_store import CardExtract, CardStore
from application_pipeline.llm.quota import QuotaWall
from application_pipeline.llm.types import (
    CallUsage,
    ClassifyItem,
    ExtractorMalformedError,
    ExtractorMalformedJSONError,
    RelevanceVerdict,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.types import PositionStub

if TYPE_CHECKING:
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
    ) -> None:
        self._extractor = extractor
        self._quota_wall = quota_wall
        self._card_store = card_store
        self._run_log = run_log
        self._failures_dir = failures_dir
        self.freshness_gate: FreshnessGate | None = freshness_gate

    def enrich(
        self, listing_id: int, stub: PositionStub, body: str
    ) -> RelevanceVerdict | None:
        """Classify and write CardStore.

        Returns the verdict on success, or None when dropped by the post-LLM
        Freshness Gate arm.
        Raises ExtractorMalformedError / ExtractorMalformedJSONError on malformed LLM
        output after stashing the error text, so callers do not mark .seen.json.
        """
        item = ClassifyItem(
            title=stub.title,
            raw_description=body,
            company=stub.company,
            location=stub.location,
            posted_date=stub.posted_date,
        )
        try:
            verdicts, _ = self._extractor.classify_relevance([item])
            verdict = verdicts[0]
            assert verdict is not None
        except (ExtractorMalformedError, ExtractorMalformedJSONError) as exc:
            self._stash_malformed(stub, exc)
            self._run_log.event(
                "llm_enricher",
                "classify_malformed",
                url=stub.url,
                source=stub.source,
                error=str(exc),
            )
            raise

        if verdict.matches:
            assert verdict.header is not None
            assert verdict.summary is not None

            if self.freshness_gate is not None:
                updated_stub = dataclasses.replace(
                    stub, posted_date=_parse_header_date(verdict.header)
                )
                if not self.freshness_gate.admit(
                    updated_stub, gate_arm="post_llm", deadline=None
                ):
                    return None

            self._card_store.put(
                listing_id,
                CardExtract(header=verdict.header, summary=verdict.summary),
            )

        return verdict

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
