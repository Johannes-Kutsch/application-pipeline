"""Tests for LLM Enricher orchestrator and body strip helper."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline.dedup import load as dedup_load
from application_pipeline.dedup.store import DeduplicationStore
from application_pipeline.extracts.card_store import load_card_store
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.llm.body_strip import strip_to_text
from application_pipeline.llm.quota import QuotaWall
from application_pipeline.llm.types import (
    AppliedClassifyOutcome,
    CallUsage,
    ExtractorBatchMalformedError,
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


def _make_enricher_with_dedup(
    *,
    extractor: object,
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> tuple[LLMEnricher, DeduplicationStore]:
    card_store = load_card_store(tmp_path / "extracts.json")
    dedup = dedup_load(tmp_path / ".seen.json")
    enricher = LLMEnricher(
        extractor=extractor,  # type: ignore[arg-type]
        quota_wall=QuotaWall(),
        card_store=card_store,
        run_log=run_log,
        failures_dir=tmp_path / "failures",
        dedup_store=dedup,
    )
    return enricher, dedup


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


def test_enricher_matched_returns_applied_outcome_and_writes_card_store(
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

    result = enricher.enrich([(1, stub, body)])

    assert isinstance(result, AppliedClassifyOutcome)
    assert [item.state for item in result.items] == ["matched"]
    assert result.matched_listings == [(1, stub)]

    card = load_card_store(tmp_path / "extracts.json").get(1)
    assert card is not None
    assert card.header == "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01"
    assert card.summary == "Great ML role."
    assert card.body == body


def test_enricher_matched_item_exposes_pool_admission_data_and_persists_dedup(
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

    enricher, dedup = _make_enricher_with_dedup(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/1",
        title="Senior Python Engineer",
        source="test",
        company="Acme",
        location="Hamburg",
    )

    with dedup.run_scope():
        dedup.is_seen(stub)
        result = enricher.enrich([(1, stub, body)])

    matched = result.items[0].matched_listing
    assert matched is not None
    assert matched.listing_id == 1
    assert matched.stub == stub

    card = load_card_store(tmp_path / "extracts.json").get(1)
    assert card is not None
    assert card.body == body

    reloaded = dedup_load(tmp_path / ".seen.json")
    assert reloaded.is_seen(stub).kind == "judge_pending"


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
        enricher.enrich([(99, stub, body)])

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
        enricher.enrich([(99, stub, body)])

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
        enricher.enrich([(99, stub, body)])

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


def test_enricher_batch_malformed_error_produces_md_file_without_prompt_or_response(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    error_msg = "batch response could not be parsed"
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorBatchMalformedError(error_msg)

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/batch",
        title="Batch Job",
        source="batch_src",
    )

    with pytest.raises(ExtractorBatchMalformedError):
        enricher.enrich([(1, stub, "body")])

    slug = "example.com-job-batch"
    stash_path = tmp_path / "failures" / "malformed" / f"batch_src-{slug}.md"
    assert stash_path.exists(), f"Expected markdown stash file at {stash_path}"
    content = stash_path.read_text(encoding="utf-8")
    assert "**Source:** batch_src" in content
    assert "**URL:** https://example.com/job/batch" in content
    assert f"**Error:** {error_msg}" in content
    assert "## Prompt" not in content
    assert "## Raw response" not in content
    assert "## CLI stderr" not in content


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
        enricher.enrich([(99, stub, body)])

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

    result = enricher.enrich([(1, stub, body)])

    assert [item.state for item in result.items] == ["expired"]
    assert card_store.get(1) is None


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

    enricher.enrich([(1, stub, body)])

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

    result = enricher.enrich([(1, stub, body)])

    assert [item.state for item in result.items] == ["matched"]
    card = card_store.get(1)
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

    result = enricher.enrich([(1, stub, body)])

    assert [item.state for item in result.items] == ["matched"]
    assert card_store.get(1) is not None


# ---------------------------------------------------------------------------
# LLMEnricher: batch interface
# ---------------------------------------------------------------------------


def test_enrich_accepts_list_of_items_and_returns_structured_outcome(
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

    results = enricher.enrich([(1, stub, body)])

    assert isinstance(results, AppliedClassifyOutcome)
    assert len(results.items) == 1
    assert results.items[0].state == "matched"
    card = load_card_store(tmp_path / "extracts.json").get(1)
    assert card is not None
    assert card.header == "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01"


# ---------------------------------------------------------------------------
# LLMEnricher: batch routing — mixed verdicts
# ---------------------------------------------------------------------------


def test_enrich_batch_routes_match_reject_none_independently(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    """Matches write card store + mark_matched; rejections expose a rejected outcome; None untouched."""
    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        [
            RelevanceVerdict(
                matches=True,
                header="ML Engineer\nAcme · Berlin · remote\n2024-06-01",
                summary="Good role.",
            ),
            RelevanceVerdict(matches=False),
            None,
        ],
        _call_usage(),
    )

    enricher, dedup = _make_enricher_with_dedup(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub_match = PositionStub(
        url="https://example.com/job/match",
        title="ML Engineer",
        source="test",
        company="Acme",
        location="Berlin",
    )
    stub_reject = PositionStub(
        url="https://example.com/job/reject",
        title="Sales Manager",
        source="test",
    )
    stub_none = PositionStub(
        url="https://example.com/job/none",
        title="Unknown",
        source="test",
    )

    with dedup.run_scope():
        dedup.is_seen(stub_match)
        dedup.is_seen(stub_reject)
        dedup.is_seen(stub_none)

        results = enricher.enrich(
            [
                (1, stub_match, "ML body"),
                (2, stub_reject, "Sales body"),
                (3, stub_none, "Unknown body"),
            ]
        )

    assert [item.state for item in results.items] == [
        "matched",
        "rejected",
        "retryable",
    ]
    assert results.matched_listings == [(1, stub_match)]

    card_store = load_card_store(tmp_path / "extracts.json")
    assert card_store.get(1) is not None, "match should write card"
    assert card_store.get(2) is None, "rejection should not write card"
    assert card_store.get(3) is None, "None verdict should not write card"

    seen_data = json.loads((tmp_path / ".seen.json").read_text(encoding="utf-8"))
    assert any(
        stub_match.url in r.get("urls", []) and r["status"] == "matched"
        for r in seen_data.values()
    ), "match listing should be marked matched"
    assert any(
        stub_reject.url in r.get("urls", []) and r["status"] == "out_of_domain"
        for r in seen_data.values()
    ), "reject listing should be marked out_of_domain"
    assert not any(stub_none.url in r.get("urls", []) for r in seen_data.values()), (
        "None verdict listing should be evicted (never promoted from pending)"
    )


# ---------------------------------------------------------------------------
# LLMEnricher: per-item freshness gate — stale does not block fresh
# ---------------------------------------------------------------------------


def test_enrich_per_item_freshness_gate_stale_does_not_block_fresh(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    stale_header = "Old Role\nCorp · Berlin · remote\n2025-12-15 · mid · —"
    fresh_header = "Fresh Role\nCorp · Berlin · remote\n2026-01-10 · mid · —"

    extractor = MagicMock()
    extractor.classify_relevance.return_value = (
        [
            RelevanceVerdict(matches=True, header=stale_header, summary="Old."),
            RelevanceVerdict(matches=True, header=fresh_header, summary="Fresh."),
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
    stub_stale = PositionStub(
        url="https://example.com/job/stale",
        title="Old Role",
        source="test",
        posted_date=None,
    )
    stub_fresh = PositionStub(
        url="https://example.com/job/fresh",
        title="Fresh Role",
        source="test",
        posted_date=None,
    )

    results = enricher.enrich(
        [(1, stub_stale, "old body"), (2, stub_fresh, "fresh body")]
    )

    assert [item.state for item in results.items] == ["expired", "matched"]
    assert card_store.get(1) is None, "stale match should not write card"
    assert card_store.get(2) is not None, "fresh match should write card"


# ---------------------------------------------------------------------------
# LLMEnricher: malformed stashing — once per batch call
# ---------------------------------------------------------------------------


def test_enrich_malformed_stash_written_once_for_batch(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    error_msg = "batch classify failed"
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorMalformedError(error_msg)

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub1 = PositionStub(url="https://example.com/job/a", title="Job A", source="src")
    stub2 = PositionStub(url="https://example.com/job/b", title="Job B", source="src")

    with pytest.raises(ExtractorMalformedError):
        enricher.enrich([(1, stub1, "body a"), (2, stub2, "body b")])

    malformed_dir = tmp_path / "failures" / "malformed"
    assert malformed_dir.exists()
    stash_files = list(malformed_dir.glob("*.md"))
    assert len(stash_files) == 1, f"Expected 1 stash file, got {len(stash_files)}"
