"""LLM Enricher — fetch + strip + classify + write CardStore."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

from application_pipeline.content_gate import ContentGate
from application_pipeline.extracts.card_store import CardExtract, CardStore
from application_pipeline.llm.body_strip import strip_to_text
from application_pipeline.llm.quota import QuotaWall
from application_pipeline.llm.types import (
    CallUsage,
    ClassifyItem,
    ExtractorMalformedError,
    ExtractorMalformedJSONError,
    RelevanceVerdictV2,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.types import PositionStub
from application_pipeline.run_metrics import RunMetrics

_TOKEN_CAP_DEFAULT = 8_000
_CHARS_PER_TOKEN = 4


@runtime_checkable
class LLMExtractorV2(Protocol):
    def classify_relevance_v2(
        self, item: ClassifyItem
    ) -> tuple[RelevanceVerdictV2, CallUsage]: ...


@dataclass
class _FetchedPosition:
    stub: PositionStub
    raw_description: str

    @property
    def title(self) -> str:
        return self.stub.title


class LLMEnricher:
    """Orchestrate HTTP fetch → body strip → content gate → classify → CardStore write."""

    def __init__(
        self,
        *,
        extractor: LLMExtractorV2,
        quota_wall: QuotaWall,
        card_store: CardStore,
        run_log: RunLog,
        run_metrics: RunMetrics,
        failures_dir: Path,
        token_cap: int = _TOKEN_CAP_DEFAULT,
    ) -> None:
        self._extractor = extractor
        self._quota_wall = quota_wall
        self._card_store = card_store
        self._run_log = run_log
        self._content_gate = ContentGate(metrics=run_metrics, run_log=run_log)
        self._failures_dir = failures_dir
        self._token_cap = token_cap
        self._http = httpx.Client(follow_redirects=True)

    def enrich(
        self, stub: PositionStub, body_selector: str | None
    ) -> RelevanceVerdictV2 | None:
        """Fetch, strip, gate, classify and write CardStore.

        Returns the verdict on success, or None when the position was gated
        (empty body, HTTP error, or oversized body — oversized also stashes raw HTML).
        Raises ExtractorMalformedError / ExtractorMalformedJSONError on malformed LLM
        output after stashing the error text, so callers do not mark .seen.json.
        """
        html = self._fetch(stub)
        if html is None:
            return None

        text = strip_to_text(html, body_selector)

        if len(text) > self._token_cap * _CHARS_PER_TOKEN:
            self._stash_failure("oversized", stub, html)
            self._run_log.event(
                "llm_enricher",
                "body_oversized",
                url=stub.url,
                source=stub.source,
                body_len=len(text),
            )
            return None

        position = _FetchedPosition(stub=stub, raw_description=text)
        if not self._content_gate.admit(position):
            return None

        item = ClassifyItem(
            title=stub.title,
            raw_description=text,
            company=stub.company,
            location=stub.location,
            posted_date=stub.posted_date,
        )
        try:
            verdict, _ = self._extractor.classify_relevance_v2(item)
        except (ExtractorMalformedError, ExtractorMalformedJSONError) as exc:
            self._stash_failure("malformed", stub, str(exc), ext="txt")
            self._run_log.event(
                "llm_enricher",
                "classify_malformed",
                url=stub.url,
                source=stub.source,
                error=str(exc),
            )
            raise

        if verdict.in_domain:
            assert verdict.header is not None
            assert verdict.summary is not None
            self._card_store.put(
                stub.url,
                CardExtract(header=verdict.header, summary=verdict.summary),
            )

        return verdict

    def _fetch(self, stub: PositionStub) -> str | None:
        try:
            response = self._http.get(stub.url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError:
            return None

    def _stash_failure(
        self, kind: str, stub: PositionStub, content: str, *, ext: str = "html"
    ) -> None:
        stash_dir = self._failures_dir / kind
        stash_dir.mkdir(parents=True, exist_ok=True)
        slug = stub.url.replace("https://", "").replace("http://", "").replace("/", "-")
        path = stash_dir / f"{stub.source}-{slug}.{ext}"
        path.write_text(content, encoding="utf-8")
