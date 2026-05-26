"""Tests for LLM Enricher orchestrator and body strip helper."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
        "<article>Senior Data Engineer – Python, Spark, and Kafka.</article>"
        "</body></html>"
    )
    result = strip_to_text(html, None)
    assert "Senior Data Engineer" in result


# ---------------------------------------------------------------------------
# LLMEnricher: end-to-end in-domain happy path
# ---------------------------------------------------------------------------


def test_enricher_matched_returns_verdict_and_writes_card_store(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "Senior Python Engineer – remote ML role."

    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        [
            RelevanceVerdict(
                matches=True,
                header="Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01",
                summary="Great ML role.",
            )
        ],
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

    result = enricher.enrich(stub, body)

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
# LLMEnricher: malformed LLM output stashed to failures/malformed/
# ---------------------------------------------------------------------------


def test_enricher_stashes_malformed_llm_output(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "Software Engineer role."
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
        enricher.enrich(stub, body)

    slug = "example.com-job-99"
    stash_path = tmp_path / "failures" / "malformed" / f"test_src-{slug}.md"
    assert stash_path.exists(), f"Expected stash file at {stash_path}"
    assert error_msg in stash_path.read_text(encoding="utf-8")
    txt_path = tmp_path / "failures" / "malformed" / f"test_src-{slug}.txt"
    assert not txt_path.exists(), "Legacy .txt file must not be produced"


# ---------------------------------------------------------------------------
# LLMEnricher: malformed LLM output — .md format with structured sections
# ---------------------------------------------------------------------------


def test_enricher_malformed_error_produces_md_file_with_all_sections(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "Software Engineer role."
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
        enricher.enrich(stub, body)

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


def test_enricher_malformed_json_error_produces_md_file_with_cli_sections(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "DevOps role."
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
        enricher.enrich(stub, body)

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
# LLMEnricher: malformed LLM output emits a log event
# ---------------------------------------------------------------------------


def test_enricher_malformed_llm_output_emits_log_event(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "Some job."
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
        enricher.enrich(stub, body)

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


def _make_freshness_gate(tmp_path: Path, run_log: RunLog) -> FreshnessGate:
    dedup = dedup_load(tmp_path / ".seen.json")
    return FreshnessGate(
        anchored_today=_ANCHORED_TODAY,
        max_listing_age_days=_MAX_AGE,
        dedup=dedup,
        display=FakeStatusDisplay(),
        run_log=run_log,
    )


def _read_freshness_transcripts(tmp_path: Path) -> list[dict]:
    path = tmp_path / "logs" / "pipeline" / "freshness.transcripts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_enricher_drops_listing_when_llm_infers_stale_posted_date(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "Python Engineer role, posted months ago."

    # LLM infers a stale posted_date in the header (31 days before ANCHORED_TODAY)
    stale_header = (
        "Python Engineer\nAcme · Hamburg · remote\n2025-12-15 · senior · €80k"
    )
    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        [
            RelevanceVerdict(
                matches=True,
                header=stale_header,
                summary="Old ML role.",
            )
        ],
        _call_usage(),
    )

    gate = _make_freshness_gate(tmp_path, run_log)
    card_store = load_card_store(tmp_path / "extracts.json")
    enricher = LLMEnricher(
        extractor=extractor,  # type: ignore[arg-type]
        quota_wall=QuotaWall(),
        card_store=card_store,
        run_log=run_log,
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

    result = enricher.enrich(stub, body)

    assert result is None
    assert card_store.get(stub.url) is None


def test_enricher_freshness_drop_records_post_llm_transcript(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "Old role."
    stale_header = "ML Engineer\nCorp · Berlin · hybrid\n2025-12-15 · mid · —"
    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        [RelevanceVerdict(matches=True, header=stale_header, summary="Stale role.")],
        _call_usage(),
    )

    gate = _make_freshness_gate(tmp_path, run_log)
    card_store = load_card_store(tmp_path / "extracts.json")
    enricher = LLMEnricher(
        extractor=extractor,  # type: ignore[arg-type]
        quota_wall=QuotaWall(),
        card_store=card_store,
        run_log=run_log,
        failures_dir=tmp_path / "failures",
        freshness_gate=gate,
    )
    stub = PositionStub(
        url="https://example.com/job/stale2",
        title="ML Engineer",
        source="test",
        posted_date=None,
    )

    enricher.enrich(stub, body)

    rows = _read_freshness_transcripts(tmp_path)
    assert len(rows) == 1
    assert rows[0]["gate_arm"] == "post_llm"
    assert rows[0]["passes"] is False
    assert rows[0]["posted_date"] == "2025-12-15"


def test_enricher_fresh_inferred_date_renders_card_normally(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "Fresh ML role posted recently."

    # posted_date 5 days ago – within MAX_AGE=30
    fresh_header = "Data Scientist\nAcme · Hamburg · remote\n2026-01-10 · senior · €90k"
    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        [
            RelevanceVerdict(
                matches=True,
                header=fresh_header,
                summary="Good ML role.",
            )
        ],
        _call_usage(),
    )

    gate = _make_freshness_gate(tmp_path, run_log)
    card_store = load_card_store(tmp_path / "extracts.json")
    enricher = LLMEnricher(
        extractor=extractor,  # type: ignore[arg-type]
        quota_wall=QuotaWall(),
        card_store=card_store,
        run_log=run_log,
        failures_dir=tmp_path / "failures",
        freshness_gate=gate,
    )
    stub = PositionStub(
        url="https://example.com/job/fresh",
        title="Data Scientist",
        source="test",
        posted_date=None,
    )

    result = enricher.enrich(stub, body)

    assert result is not None
    assert result.matches is True
    card = card_store.get(stub.url)
    assert card is not None
    assert card.header == fresh_header


def test_enricher_no_parseable_date_in_header_passes_post_llm_gate(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "Undated role."

    # Header line 3 has no date (LLM dropped the segment)
    no_date_header = "Backend Engineer\nCorp · Munich · on-site\nseniority: mid · —"
    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        [
            RelevanceVerdict(
                matches=True,
                header=no_date_header,
                summary="Undated backend role.",
            )
        ],
        _call_usage(),
    )

    gate = _make_freshness_gate(tmp_path, run_log)
    card_store = load_card_store(tmp_path / "extracts.json")
    enricher = LLMEnricher(
        extractor=extractor,  # type: ignore[arg-type]
        quota_wall=QuotaWall(),
        card_store=card_store,
        run_log=run_log,
        failures_dir=tmp_path / "failures",
        freshness_gate=gate,
    )
    stub = PositionStub(
        url="https://example.com/job/noddate",
        title="Backend Engineer",
        source="test",
        posted_date=None,
    )

    result = enricher.enrich(stub, body)

    assert result is not None
    assert result.matches is True
    assert card_store.get(stub.url) is not None
