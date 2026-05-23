"""Tests for LLM Enricher orchestrator and body strip helper."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from fake_status_display import FakeStatusDisplay

from application_pipeline.dedup import load as dedup_load
from application_pipeline.extracts.card_store import load_card_store
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.llm.body_strip import strip_to_text
from application_pipeline.llm.quota import QuotaWall
from application_pipeline.llm.types import (
    CallUsage,
    ExtractorMalformedError,
    ExtractorMalformedJSONError,
    RelevanceVerdict,
)
from application_pipeline.llm_enricher import LLMEnricher
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.types import PositionStub
from application_pipeline.run_metrics import RunMetrics

_ANCHORED_TODAY = date(2026, 1, 15)
_MAX_AGE = 30


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
        "<article>Senior Data Engineer â€” Python, Spark, and Kafka.</article>"
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
        "<div class='job'>Senior Python Engineer â€” remote ML role.</div>"
        "</body></html>"
    )
    respx.get("https://example.com/job/1").mock(
        return_value=httpx.Response(200, text=html)
    )

    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        RelevanceVerdict(
            matches=True,
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
    assert result.matches is True
    assert (
        result.header == "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01"
    )
    assert result.summary == "Great ML role."

    card = load_card_store(tmp_path / "extracts.json").get(stub.url)
    assert card is not None
    assert card.header == "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01"
    assert card.summary == "Great ML role."


# ---------------------------------------------------------------------------
# LLMEnricher: empty body dropped by ContentGate â€” no LLM call
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
    extractor.classify_relevance.assert_not_called()


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
    extractor.classify_relevance.return_value = (
        RelevanceVerdict(
            matches=True,
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
    assert result.matches is True
    call_args = extractor.classify_relevance.call_args
    item = call_args.args[0]
    assert "Data Engineer at final destination" in item.raw_description


# ---------------------------------------------------------------------------
# LLMEnricher: malformed LLM output stashed to failures/malformed/
# ---------------------------------------------------------------------------


@respx.mock
def test_enricher_stashes_malformed_llm_output(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    html = "<html><body><div class='job'>Software Engineer role.</div></body></html>"
    respx.get("https://example.com/job/99").mock(
        return_value=httpx.Response(200, text=html)
    )

    error_msg = (
        "classify_relevance: header must be a non-empty string for in-domain verdict"
    )
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorMalformedError(error_msg)

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/99",
        title="Software Engineer",
        source="test_src",
    )

    with pytest.raises(ExtractorMalformedError):
        enricher.enrich(stub, ".job")

    slug = "example.com-job-99"
    stash_path = tmp_path / "failures" / "malformed" / f"test_src-{slug}.md"
    assert stash_path.exists(), f"Expected stash file at {stash_path}"
    assert error_msg in stash_path.read_text(encoding="utf-8")
    txt_path = tmp_path / "failures" / "malformed" / f"test_src-{slug}.txt"
    assert not txt_path.exists(), "Legacy .txt file must not be produced"


# ---------------------------------------------------------------------------
# LLMEnricher: malformed LLM output — .md format with structured sections
# ---------------------------------------------------------------------------


@respx.mock
def test_enricher_malformed_error_produces_md_file_with_all_sections(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    html = "<html><body><div class='job'>Software Engineer role.</div></body></html>"
    respx.get("https://example.com/job/99").mock(
        return_value=httpx.Response(200, text=html)
    )

    error_msg = "classify_relevance: header must be a non-empty string"
    prompt_text = "You are a relevance classifier. Evaluate this job."
    raw_resp = "<result>{bad json}</result>"
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorMalformedError(
        error_msg, prompt=prompt_text, raw_response=raw_resp
    )

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/99",
        title="Software Engineer",
        source="test_src",
    )

    with pytest.raises(ExtractorMalformedError):
        enricher.enrich(stub, ".job")

    slug = "example.com-job-99"
    stash_path = tmp_path / "failures" / "malformed" / f"test_src-{slug}.md"
    assert stash_path.exists(), f"Expected markdown stash file at {stash_path}"
    content = stash_path.read_text(encoding="utf-8")
    assert "**Source:** test_src" in content
    assert "**URL:** https://example.com/job/99" in content
    assert f"**Error:** {error_msg}" in content
    assert "## Prompt" in content
    assert prompt_text in content
    assert "## Raw response" in content
    assert raw_resp in content


@respx.mock
def test_enricher_malformed_json_error_produces_md_file_with_cli_sections(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    html = "<html><body><div class='job'>DevOps role.</div></body></html>"
    respx.get("https://example.com/job/cli").mock(
        return_value=httpx.Response(200, text=html)
    )

    error_msg = "claude CLI exited with code 1"
    prompt_text = "Classify this job posting."
    stderr_text = "Error: API rate limit exceeded"
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorMalformedJSONError(
        error_msg, returncode=1, stderr=stderr_text, prompt=prompt_text
    )

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/cli",
        title="DevOps Engineer",
        source="src_cli",
    )

    with pytest.raises(ExtractorMalformedJSONError):
        enricher.enrich(stub, ".job")

    slug = "example.com-job-cli"
    stash_path = tmp_path / "failures" / "malformed" / f"src_cli-{slug}.md"
    assert stash_path.exists(), f"Expected markdown stash file at {stash_path}"
    content = stash_path.read_text(encoding="utf-8")
    assert "**Source:** src_cli" in content
    assert "**URL:** https://example.com/job/cli" in content
    assert f"**Error:** {error_msg}" in content
    assert "## Prompt" in content
    assert prompt_text in content
    assert "## CLI stderr" in content
    assert stderr_text in content
    assert "**Returncode:** 1" in content
    assert "## Raw response" not in content


# ---------------------------------------------------------------------------
# LLMEnricher: oversized body stashed and body_oversized log event emitted
# ---------------------------------------------------------------------------


@respx.mock
def test_enricher_stashes_oversized_body_and_emits_log_event(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    big_text = "word " * 8001  # well over 8000-token cap (4 chars/token = 32000 chars)
    html = f"<html><body><div class='job'>{big_text}</div></body></html>"
    respx.get("https://example.com/job/big").mock(
        return_value=httpx.Response(200, text=html)
    )

    extractor = MagicMock()
    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/big",
        title="Big Job",
        source="src_a",
    )

    enricher.enrich(stub, ".job")

    slug = "example.com-job-big"
    stash_path = tmp_path / "failures" / "oversized" / f"src_a-{slug}.html"
    assert stash_path.exists(), f"Expected stash file at {stash_path}"
    assert big_text[:50] in stash_path.read_text(encoding="utf-8")

    events_file = tmp_path / "logs" / "llm" / "enricher.events.jsonl"
    assert events_file.exists()
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    oversized_events = [e for e in events if e.get("event") == "body_oversized"]
    assert len(oversized_events) == 1
    assert oversized_events[0]["source"] == "src_a"
    assert oversized_events[0]["url"] == stub.url


# ---------------------------------------------------------------------------
# LLMEnricher: re-firing oversized URL overwrites stash file (no duplicates)
# ---------------------------------------------------------------------------


@respx.mock
def test_enricher_oversized_refire_overwrites_stash(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    big_text = "word " * 8001
    html = f"<html><body><div class='job'>{big_text}</div></body></html>"
    respx.get("https://example.com/job/refire").mock(
        return_value=httpx.Response(200, text=html)
    )

    extractor = MagicMock()
    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/refire",
        title="Job",
        source="src_b",
    )
    slug = "example.com-job-refire"
    stash_path = tmp_path / "failures" / "oversized" / f"src_b-{slug}.html"

    enricher.enrich(stub, ".job")
    first_mtime = stash_path.stat().st_mtime

    enricher.enrich(stub, ".job")
    second_mtime = stash_path.stat().st_mtime

    assert stash_path.exists()
    assert second_mtime >= first_mtime
    assert len(list((tmp_path / "failures" / "oversized").iterdir())) == 1


# ---------------------------------------------------------------------------
# LLMEnricher: malformed LLM output emits a log event
# ---------------------------------------------------------------------------


@respx.mock
def test_enricher_malformed_llm_output_emits_log_event(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    html = "<html><body><div class='job'>Some job.</div></body></html>"
    respx.get("https://example.com/job/mal").mock(
        return_value=httpx.Response(200, text=html)
    )

    error_msg = "classify_relevance: summary must be a non-empty string"
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorMalformedError(error_msg)

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/mal",
        title="Some Job",
        source="src_c",
    )

    with pytest.raises(ExtractorMalformedError):
        enricher.enrich(stub, ".job")

    events_file = tmp_path / "logs" / "llm" / "enricher.events.jsonl"
    assert events_file.exists()
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    malformed_events = [e for e in events if e.get("event") == "classify_malformed"]
    assert len(malformed_events) == 1
    assert malformed_events[0]["source"] == "src_c"
    assert malformed_events[0]["url"] == stub.url
    assert error_msg in malformed_events[0]["error"]


# ---------------------------------------------------------------------------
# LLMEnricher: post-LLM Freshness Gate arm
# ---------------------------------------------------------------------------


def _make_freshness_gate(
    tmp_path: Path, run_log: RunLog, run_metrics: RunMetrics
) -> FreshnessGate:
    dedup = dedup_load(tmp_path / ".seen.json")
    return FreshnessGate(
        anchored_today=_ANCHORED_TODAY,
        max_listing_age_days=_MAX_AGE,
        dedup=dedup,
        metrics=run_metrics,
        run_log=run_log,
    )


def _read_freshness_transcripts(tmp_path: Path) -> list[dict]:
    path = tmp_path / "logs" / "pipeline" / "freshness.transcripts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@respx.mock
def test_enricher_drops_listing_when_llm_infers_stale_posted_date(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    html = "<html><body><div class='job'>Python Engineer role, posted months ago.</div></body></html>"
    respx.get("https://example.com/job/stale").mock(
        return_value=httpx.Response(200, text=html)
    )

    # LLM infers a stale posted_date in the header (31 days before ANCHORED_TODAY)
    stale_header = (
        "Python Engineer\nAcme · Hamburg · remote\n2025-12-15 · senior · â‚¬80k"
    )
    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        RelevanceVerdict(
            matches=True,
            header=stale_header,
            summary="Old ML role.",
        ),
        _call_usage(),
    )

    gate = _make_freshness_gate(tmp_path, run_log, run_metrics)
    card_store = load_card_store(tmp_path / "extracts.json")
    enricher = LLMEnricher(
        extractor=extractor,  # type: ignore[arg-type]
        quota_wall=QuotaWall(),
        card_store=card_store,
        run_log=run_log,
        run_metrics=run_metrics,
        failures_dir=tmp_path / "failures",
        freshness_gate=gate,
    )
    stub = PositionStub(
        url="https://example.com/job/stale",
        title="Python Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
        posted_date=None,  # no pre-LLM date
    )

    result = enricher.enrich(stub, ".job")

    assert result is None
    assert card_store.get(stub.url) is None


@respx.mock
def test_enricher_freshness_drop_records_post_enrich_transcript(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    html = "<html><body><div class='job'>Old role.</div></body></html>"
    respx.get("https://example.com/job/stale2").mock(
        return_value=httpx.Response(200, text=html)
    )

    stale_header = "ML Engineer\nCorp · Berlin · hybrid\n2025-12-15 · mid · â€”"
    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        RelevanceVerdict(matches=True, header=stale_header, summary="Stale role."),
        _call_usage(),
    )

    gate = _make_freshness_gate(tmp_path, run_log, run_metrics)
    card_store = load_card_store(tmp_path / "extracts.json")
    enricher = LLMEnricher(
        extractor=extractor,  # type: ignore[arg-type]
        quota_wall=QuotaWall(),
        card_store=card_store,
        run_log=run_log,
        run_metrics=run_metrics,
        failures_dir=tmp_path / "failures",
        freshness_gate=gate,
    )
    stub = PositionStub(
        url="https://example.com/job/stale2",
        title="ML Engineer",
        source="test",
        posted_date=None,
    )

    enricher.enrich(stub, ".job")

    rows = _read_freshness_transcripts(tmp_path)
    assert len(rows) == 1
    assert rows[0]["gate_arm"] == "post_enrich"
    assert rows[0]["passes"] is False
    assert rows[0]["posted_date"] == "2025-12-15"


@respx.mock
def test_enricher_fresh_inferred_date_renders_card_normally(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    html = "<html><body><div class='job'>Fresh ML role posted recently.</div></body></html>"
    respx.get("https://example.com/job/fresh").mock(
        return_value=httpx.Response(200, text=html)
    )

    # posted_date 5 days ago â€” within MAX_AGE=30
    fresh_header = (
        "Data Scientist\nAcme · Hamburg · remote\n2026-01-10 · senior · â‚¬90k"
    )
    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        RelevanceVerdict(
            matches=True,
            header=fresh_header,
            summary="Good ML role.",
        ),
        _call_usage(),
    )

    gate = _make_freshness_gate(tmp_path, run_log, run_metrics)
    card_store = load_card_store(tmp_path / "extracts.json")
    enricher = LLMEnricher(
        extractor=extractor,  # type: ignore[arg-type]
        quota_wall=QuotaWall(),
        card_store=card_store,
        run_log=run_log,
        run_metrics=run_metrics,
        failures_dir=tmp_path / "failures",
        freshness_gate=gate,
    )
    stub = PositionStub(
        url="https://example.com/job/fresh",
        title="Data Scientist",
        source="test",
        posted_date=None,
    )

    result = enricher.enrich(stub, ".job")

    assert result is not None
    assert result.matches is True
    card = card_store.get(stub.url)
    assert card is not None
    assert card.header == fresh_header


@respx.mock
def test_enricher_no_parseable_date_in_header_passes_post_llm_gate(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    html = "<html><body><div class='job'>Undated role.</div></body></html>"
    respx.get("https://example.com/job/noddate").mock(
        return_value=httpx.Response(200, text=html)
    )

    # Header line 3 has no date (LLM dropped the segment)
    no_date_header = "Backend Engineer\nCorp · Munich · on-site\nseniority: mid · â€”"
    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        RelevanceVerdict(
            matches=True,
            header=no_date_header,
            summary="Undated backend role.",
        ),
        _call_usage(),
    )

    gate = _make_freshness_gate(tmp_path, run_log, run_metrics)
    card_store = load_card_store(tmp_path / "extracts.json")
    enricher = LLMEnricher(
        extractor=extractor,  # type: ignore[arg-type]
        quota_wall=QuotaWall(),
        card_store=card_store,
        run_log=run_log,
        run_metrics=run_metrics,
        failures_dir=tmp_path / "failures",
        freshness_gate=gate,
    )
    stub = PositionStub(
        url="https://example.com/job/noddate",
        title="Backend Engineer",
        source="test",
        posted_date=None,
    )

    result = enricher.enrich(stub, ".job")

    assert result is not None
    assert result.matches is True
    assert card_store.get(stub.url) is not None
