"""Tests for LLM Enricher orchestrator and body strip helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from fake_status_display import FakeStatusDisplay

from application_pipeline.extracts.card_store import load_card_store
from application_pipeline.llm.body_strip import strip_to_text
from application_pipeline.llm.quota import QuotaWall
from application_pipeline.llm.types import CallUsage, RelevanceVerdictV2
from application_pipeline.llm_enricher import LLMEnricher
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.types import PositionStub
from application_pipeline.run_metrics import RunMetrics


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path / "logs")


@pytest.fixture
def run_metrics(tmp_path: Path, run_log: RunLog) -> RunMetrics:
    return RunMetrics(FakeStatusDisplay(), run_log=run_log)


def _call_usage() -> CallUsage:
    return CallUsage(
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=0,
        cost_usd=0.001,
        duration_s=0.5,
    )


def _make_enricher(
    *,
    extractor: object,
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> LLMEnricher:
    card_store = load_card_store(tmp_path / "extracts.json")
    return LLMEnricher(
        extractor=extractor,  # type: ignore[arg-type]
        quota_wall=QuotaWall(),
        card_store=card_store,
        run_log=run_log,
        run_metrics=run_metrics,
        failures_dir=tmp_path / "failures",
    )


# ---------------------------------------------------------------------------
# strip_to_text
# ---------------------------------------------------------------------------


def test_strip_to_text_with_selector_returns_matched_node_text() -> None:
    html = (
        "<html><body>"
        "<div class='other'>Noise</div>"
        "<div class='job-body'>Python Engineer role</div>"
        "</body></html>"
    )
    result = strip_to_text(html, ".job-body")
    assert result == "Python Engineer role"


def test_strip_to_text_without_selector_falls_back_to_trafilatura() -> None:
    html = (
        "<html><body>"
        "<article>Senior Data Engineer — Python, Spark, and Kafka.</article>"
        "</body></html>"
    )
    result = strip_to_text(html, None)
    assert "Senior Data Engineer" in result


# ---------------------------------------------------------------------------
# LLMEnricher: end-to-end in-domain happy path
# ---------------------------------------------------------------------------


@respx.mock
def test_enricher_in_domain_returns_verdict_and_writes_card_store(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    html = (
        "<html><body>"
        "<div class='job'>Senior Python Engineer — remote ML role.</div>"
        "</body></html>"
    )
    respx.get("https://example.com/job/1").mock(
        return_value=httpx.Response(200, text=html)
    )

    extractor = MagicMock()
    extractor.classify_relevance_v2.return_value = (
        RelevanceVerdictV2(
            in_domain=True,
            header="Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01",
            summary="Great ML role.",
        ),
        _call_usage(),
    )

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/1",
        title="Senior Python Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
    )

    result = enricher.enrich(stub, ".job")

    assert result is not None
    assert result.in_domain is True
    assert (
        result.header == "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01"
    )
    assert result.summary == "Great ML role."

    card = load_card_store(tmp_path / "extracts.json").get(stub.url)
    assert card is not None
    assert card.header == "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01"
    assert card.summary == "Great ML role."


# ---------------------------------------------------------------------------
# LLMEnricher: empty body dropped by ContentGate — no LLM call
# ---------------------------------------------------------------------------


@respx.mock
def test_enricher_drops_empty_body_without_llm_call(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    html = "<html><body><div class='job'>   </div></body></html>"
    respx.get("https://example.com/job/2").mock(
        return_value=httpx.Response(200, text=html)
    )

    extractor = MagicMock()
    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/2", title="Test Job", source="test"
    )

    result = enricher.enrich(stub, ".job")

    assert result is None
    extractor.classify_relevance_v2.assert_not_called()


# ---------------------------------------------------------------------------
# LLMEnricher: HTTP redirects followed silently
# ---------------------------------------------------------------------------


@respx.mock
def test_enricher_follows_http_redirect_and_uses_final_page_content(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    final_html = (
        "<html><body>"
        "<div class='job'>Data Engineer at final destination.</div>"
        "</body></html>"
    )
    respx.get("https://redirect.example.com/old").mock(
        return_value=httpx.Response(
            301, headers={"Location": "https://final.example.com/job"}
        )
    )
    respx.get("https://final.example.com/job").mock(
        return_value=httpx.Response(200, text=final_html)
    )

    extractor = MagicMock()
    extractor.classify_relevance_v2.return_value = (
        RelevanceVerdictV2(
            in_domain=True,
            header="Data Engineer\nCorp · Berlin · on-site\n2024-01-01",
            summary="Good role.",
        ),
        _call_usage(),
    )

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://redirect.example.com/old", title="Data Engineer", source="test"
    )

    result = enricher.enrich(stub, ".job")

    assert result is not None
    assert result.in_domain is True
    call_args = extractor.classify_relevance_v2.call_args
    item = call_args.args[0]
    assert "Data Engineer at final destination" in item.raw_description
