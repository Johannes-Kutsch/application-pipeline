"""Tests for LLM Enricher orchestrator."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from application_pipeline.dedup import load as dedup_load
from application_pipeline.dedup.store import DeduplicationStore
from application_pipeline.extracts.card_store import load_card_store
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.llm.quota import QuotaWall
from application_pipeline.llm.types import (
    AppliedClassifyOutcome,
    ExtractorBatchMalformedError,
    ExtractorMalformedError,
    ExtractorMalformedJSONError,
    ExtractorUnreachableError,
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
    from fake_status_display import FakeStatusDisplay

    return RunMetrics(FakeStatusDisplay(), run_log=run_log)


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
# LLMEnricher: end-to-end in-domain happy path
# ---------------------------------------------------------------------------


def test_enricher_matched_returns_applied_outcome_and_writes_card_store(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "Senior Python Engineer – remote ML role."

    extractor = MagicMock()
    extractor.classify_relevance.return_value = [
        RelevanceVerdict(
            matches=True,
            header="Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01",
            summary="Great ML role.",
        )
    ]

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
    extractor.classify_relevance.return_value = [
        RelevanceVerdict(
            matches=True,
            header="Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01",
            summary="Great ML role.",
        )
    ]

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


def _runtime_log_path(tmp_path: Path) -> Path:
    path = tmp_path / "logs" / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("runtime output\n", encoding="utf-8")
    return path


def _record_verdict_stash_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def _fake_stash(**kwargs: object) -> Path:
        calls.append(kwargs)
        return Path("fake-stash.md")

    monkeypatch.setattr(
        "application_pipeline.llm_enricher.stash_malformed_classify_verdict",
        _fake_stash,
    )
    return calls


def _record_exception_stash_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def _fake_stash(**kwargs: object) -> Path:
        calls.append(kwargs)
        return Path("fake-stash.md")

    monkeypatch.setattr(
        "application_pipeline.llm_enricher.stash_malformed_classify_exception",
        _fake_stash,
    )
    return calls


def _assert_stashed_error(
    error: object,
    expected_type: type[BaseException],
    expected_message: str,
) -> None:
    assert isinstance(error, expected_type)
    assert str(error) == expected_message


def test_enricher_stashes_malformed_verdict_references_agent_runtime_log(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_log = _runtime_log_path(tmp_path)
    stash_calls = _record_verdict_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.return_value = [None]
    extractor.last_classify_log_path = runtime_log

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/99",
        title="Software Engineer",
        source="test_src",
    )

    result = enricher.enrich([(99, stub, "Raw description body")])

    assert [item.state for item in result.items] == ["retryable"]
    assert stash_calls == [
        {
            "filesystem_root": tmp_path / "failures",
            "stub": stub,
            "agent_runtime_log_pointer": runtime_log,
        }
    ]


def test_enricher_stashes_malformed_verdict_with_opaque_agent_runtime_pointer(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_pointer = "agent-runtime://classify/run-42?event=3#result"
    stash_calls = _record_verdict_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.return_value = [None]
    extractor.last_classify_log_path = runtime_pointer

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/opaque-pointer",
        title="Software Engineer",
        source="test_src",
    )

    result = enricher.enrich([(99, stub, "Raw description body")])

    assert [item.state for item in result.items] == ["retryable"]
    assert stash_calls == [
        {
            "filesystem_root": tmp_path / "failures",
            "stub": stub,
            "agent_runtime_log_pointer": runtime_pointer,
        }
    ]


@pytest.mark.parametrize(
    ("classify_result", "expected_error_classification", "expected_error_message"),
    [
        ([None], "malformed_classifier_verdict", "malformed classifier verdict"),
        (
            ExtractorMalformedError("header must be a non-empty string"),
            "ExtractorMalformedError",
            "header must be a non-empty string",
        ),
    ],
)
def test_enricher_malformed_stash_identifies_listing_title(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
    classify_result: list[None] | ExtractorMalformedError,
    expected_error_classification: str,
    expected_error_message: str,
) -> None:
    verdict_stash_calls = _record_verdict_stash_calls(monkeypatch)
    exception_stash_calls = _record_exception_stash_calls(monkeypatch)
    extractor = MagicMock()
    if isinstance(classify_result, Exception):
        extractor.classify_relevance.side_effect = classify_result
    else:
        extractor.classify_relevance.return_value = classify_result

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/99",
        title="Software Engineer",
        source="test_src",
    )

    result = enricher.enrich([(99, stub, "Raw description body")])

    assert [item.state for item in result.items] == ["retryable"]
    assert len(verdict_stash_calls) + len(exception_stash_calls) == 1
    stash_call = (
        verdict_stash_calls[0] if verdict_stash_calls else exception_stash_calls[0]
    )
    assert stash_call["filesystem_root"] == tmp_path / "failures"
    assert stash_call["stub"] == stub
    if isinstance(classify_result, Exception):
        error = stash_call["error"]
        assert isinstance(error, ExtractorMalformedError)
        assert type(error).__name__ == expected_error_classification
        assert str(error) == expected_error_message
    else:
        assert expected_error_classification == "malformed_classifier_verdict"
        assert expected_error_message == "malformed classifier verdict"
        assert stash_call["agent_runtime_log_pointer"] is None


def test_enricher_stashes_malformed_llm_output_and_returns_retryable(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "Software Engineer role."
    error_msg = (
        "classify_relevance: header must be a non-empty string for in-domain verdict"
    )
    stash_calls = _record_exception_stash_calls(monkeypatch)
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

    result = enricher.enrich([(99, stub, body)])

    assert [item.state for item in result.items] == ["retryable"]
    assert load_card_store(tmp_path / "extracts.json").get(99) is None
    assert len(stash_calls) == 1
    assert stash_calls[0]["filesystem_root"] == tmp_path / "failures"
    assert stash_calls[0]["stub"] == stub
    assert stash_calls[0]["agent_runtime_log_pointer"] is None
    assert stash_calls[0]["raw_description"] == body
    _assert_stashed_error(stash_calls[0]["error"], ExtractorMalformedError, error_msg)


# ---------------------------------------------------------------------------
# LLMEnricher: malformed LLM output — .md format with structured sections
# ---------------------------------------------------------------------------


def test_enricher_malformed_error_produces_retryable_md_file_with_runtime_log_reference(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_log = _runtime_log_path(tmp_path)
    body = "Software Engineer role."
    error_msg = "classify_relevance: header must be a non-empty string"
    prompt_text = "You are a relevance classifier. Evaluate this job."
    raw_resp = "<result>{bad json}</result>"
    stash_calls = _record_exception_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorMalformedError(
        error_msg, prompt=prompt_text, raw_response=raw_resp
    )
    extractor.last_classify_log_path = runtime_log

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/99",
        title="Software Engineer",
        source="test_src",
    )

    result = enricher.enrich([(99, stub, body)])

    assert [item.state for item in result.items] == ["retryable"]
    assert len(stash_calls) == 1
    assert stash_calls[0]["filesystem_root"] == tmp_path / "failures"
    assert stash_calls[0]["stub"] == stub
    assert stash_calls[0]["agent_runtime_log_pointer"] == runtime_log
    assert stash_calls[0]["raw_description"] == body
    _assert_stashed_error(stash_calls[0]["error"], ExtractorMalformedError, error_msg)


def test_enricher_malformed_json_error_produces_retryable_md_file_with_runtime_log_reference(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_log = _runtime_log_path(tmp_path)
    body = "DevOps role."
    error_msg = "claude CLI exited with code 1"
    prompt_text = "Classify this job posting."
    stderr_text = "Error: API rate limit exceeded"
    stash_calls = _record_exception_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorMalformedJSONError(
        error_msg, returncode=1, stderr=stderr_text, prompt=prompt_text
    )
    extractor.last_classify_log_path = runtime_log

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/cli",
        title="DevOps Engineer",
        source="src_cli",
    )

    result = enricher.enrich([(99, stub, body)])

    assert [item.state for item in result.items] == ["retryable"]
    assert len(stash_calls) == 1
    assert stash_calls[0]["filesystem_root"] == tmp_path / "failures"
    assert stash_calls[0]["stub"] == stub
    assert stash_calls[0]["agent_runtime_log_pointer"] == runtime_log
    assert stash_calls[0]["raw_description"] == body
    _assert_stashed_error(
        stash_calls[0]["error"], ExtractorMalformedJSONError, error_msg
    )


def test_enricher_malformed_json_error_includes_raw_model_output_without_prompt_or_raw_description(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_log = _runtime_log_path(tmp_path)
    body = "DevOps role."
    error_msg = "claude CLI exited with code 1"
    prompt_text = "Classify this job posting."
    stderr_text = "Error: API rate limit exceeded"
    raw_resp = "<result>{bad json}</result>"
    stash_calls = _record_exception_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorMalformedJSONError(
        error_msg,
        returncode=1,
        stderr=stderr_text,
        prompt=prompt_text,
        raw_response=raw_resp,
    )
    extractor.last_classify_log_path = runtime_log

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/cli",
        title="DevOps Engineer",
        source="src_cli",
    )

    result = enricher.enrich([(99, stub, body)])

    assert [item.state for item in result.items] == ["retryable"]
    assert len(stash_calls) == 1
    assert stash_calls[0]["filesystem_root"] == tmp_path / "failures"
    assert stash_calls[0]["stub"] == stub
    assert stash_calls[0]["agent_runtime_log_pointer"] == runtime_log
    assert stash_calls[0]["raw_description"] == body
    _assert_stashed_error(
        stash_calls[0]["error"], ExtractorMalformedJSONError, error_msg
    )


def test_enricher_malformed_exception_stashes_sanitized_raw_output(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_log = _runtime_log_path(tmp_path)
    body = "DISTINCTIVE RAW DESCRIPTION BODY 1053"
    prompt_text = "PROMPT TEXT 1053"
    useful_raw_output = "provider note: trailing comma near summary field"
    raw_resp = (
        f"<verdict>{{bad json}}</verdict>\n{useful_raw_output}\n{prompt_text}\n{body}"
    )
    stash_calls = _record_exception_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorMalformedError(
        "classify_relevance: malformed verdict payload",
        prompt=prompt_text,
        raw_response=raw_resp,
    )
    extractor.last_classify_log_path = runtime_log

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/sanitized-raw-output",
        title="Software Engineer",
        source="test_src",
    )

    result = enricher.enrich([(99, stub, body)])

    assert [item.state for item in result.items] == ["retryable"]
    assert len(stash_calls) == 1
    assert stash_calls[0]["filesystem_root"] == tmp_path / "failures"
    assert stash_calls[0]["stub"] == stub
    assert stash_calls[0]["agent_runtime_log_pointer"] == runtime_log
    assert stash_calls[0]["raw_description"] == body
    _assert_stashed_error(
        stash_calls[0]["error"],
        ExtractorMalformedError,
        "classify_relevance: malformed verdict payload",
    )


def test_enricher_batch_malformed_stash_references_agent_runtime_log(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_log = _runtime_log_path(tmp_path)
    error_msg = "batch response could not be parsed"
    stash_calls = _record_exception_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorBatchMalformedError(error_msg)
    extractor.last_classify_log_path = runtime_log

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/batch",
        title="Batch Job",
        source="batch_src",
    )

    result = enricher.enrich([(1, stub, "body")])

    assert [item.state for item in result.items] == ["retryable"]
    assert len(stash_calls) == 1
    assert stash_calls[0]["filesystem_root"] == tmp_path / "failures"
    assert stash_calls[0]["stub"] == stub
    assert stash_calls[0]["agent_runtime_log_pointer"] == runtime_log
    assert stash_calls[0]["raw_description"] == "body"
    _assert_stashed_error(
        stash_calls[0]["error"], ExtractorBatchMalformedError, error_msg
    )


def test_enricher_malformed_stash_uses_current_classify_runtime_log(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_log = _runtime_log_path(tmp_path)
    current_log = (
        tmp_path / "logs" / "llm" / "agent-runtime" / "classify" / "current.log"
    )
    current_log.write_text("current runtime output\n", encoding="utf-8")
    stash_calls = _record_exception_stash_calls(monkeypatch)

    class _Extractor:
        def __init__(self) -> None:
            self.last_classify_log_path = stale_log

        def classify_relevance(self, items: list[object]) -> list[object]:
            self.last_classify_log_path = current_log
            raise ExtractorBatchMalformedError("batch response could not be parsed")

    enricher = _make_enricher(
        extractor=_Extractor(),
        tmp_path=tmp_path,
        run_log=run_log,
        run_metrics=run_metrics,
    )
    stub = PositionStub(
        url="https://example.com/job/current-log",
        title="Batch Job",
        source="batch_src",
    )

    result = enricher.enrich([(1, stub, "body")])

    assert [item.state for item in result.items] == ["retryable"]
    assert len(stash_calls) == 1
    assert stash_calls[0]["agent_runtime_log_pointer"] == current_log


def test_enricher_batch_malformed_error_returns_retryable_and_produces_md_file_without_prompt_or_response(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error_msg = "batch response could not be parsed"
    stash_calls = _record_exception_stash_calls(monkeypatch)
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

    result = enricher.enrich([(1, stub, "body")])

    assert [item.state for item in result.items] == ["retryable"]
    assert len(stash_calls) == 1
    assert stash_calls[0]["filesystem_root"] == tmp_path / "failures"
    assert stash_calls[0]["stub"] == stub
    _assert_stashed_error(
        stash_calls[0]["error"], ExtractorBatchMalformedError, error_msg
    )
    assert stash_calls[0]["agent_runtime_log_pointer"] is None
    assert stash_calls[0]["raw_description"] == "body"


# ---------------------------------------------------------------------------
# LLMEnricher: malformed LLM output emits a log event
# ---------------------------------------------------------------------------


def test_enricher_malformed_llm_output_emits_log_event_and_returns_retryable(
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

    result = enricher.enrich([(99, stub, body)])

    assert [item.state for item in result.items] == ["retryable"]

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
    extractor.classify_relevance.return_value = [
        RelevanceVerdict(
            matches=True,
            header=stale_header,
            summary="Old ML role.",
        )
    ]

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
    extractor.classify_relevance.return_value = [
        RelevanceVerdict(matches=True, header=stale_header, summary="Stale role.")
    ]

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
    extractor.classify_relevance.return_value = [
        RelevanceVerdict(
            matches=True,
            header=fresh_header,
            summary="Good ML role.",
        )
    ]

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
    extractor.classify_relevance.return_value = [
        RelevanceVerdict(
            matches=True,
            header=no_date_header,
            summary="Undated backend role.",
        )
    ]

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
    extractor.classify_relevance.return_value = [
        RelevanceVerdict(
            matches=True,
            header="Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01",
            summary="Great ML role.",
        )
    ]

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


def test_enrich_batch_keeps_none_verdict_retryable_while_later_verdicts_apply(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_log = _runtime_log_path(tmp_path)
    stash_calls = _record_verdict_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.return_value = [
        None,
        RelevanceVerdict(matches=False),
        RelevanceVerdict(
            matches=True,
            header="ML Engineer\nAcme · Berlin · remote\n2024-06-01",
            summary="Good role.",
        ),
    ]
    extractor.last_classify_log_path = runtime_log

    enricher, dedup = _make_enricher_with_dedup(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub_none = PositionStub(
        url="https://example.com/job/none-first",
        title="Unknown",
        source="test",
    )
    stub_reject = PositionStub(
        url="https://example.com/job/reject-after-none",
        title="Sales Manager",
        source="test",
    )
    stub_match = PositionStub(
        url="https://example.com/job/match-after-none",
        title="ML Engineer",
        source="test",
        company="Acme",
        location="Berlin",
    )

    with dedup.run_scope():
        dedup.is_seen(stub_none)
        dedup.is_seen(stub_reject)
        dedup.is_seen(stub_match)

        results = enricher.enrich(
            [
                (1, stub_none, "Unknown body"),
                (2, stub_reject, "Sales body"),
                (3, stub_match, "ML body"),
            ]
        )

    assert [item.state for item in results.items] == [
        "retryable",
        "rejected",
        "matched",
    ]
    assert results.items[0].matched_listing is None
    assert results.matched_listings == [(3, stub_match)]

    card_store = load_card_store(tmp_path / "extracts.json")
    assert card_store.get(1) is None
    assert card_store.get(2) is None
    assert card_store.get(3) is not None

    seen_data = json.loads((tmp_path / ".seen.json").read_text(encoding="utf-8"))
    assert not any(stub_none.url in r.get("urls", []) for r in seen_data.values())
    assert any(
        stub_reject.url in r.get("urls", []) and r["status"] == "out_of_domain"
        for r in seen_data.values()
    )
    assert any(
        stub_match.url in r.get("urls", []) and r["status"] == "matched"
        for r in seen_data.values()
    )
    assert stash_calls == [
        {
            "filesystem_root": tmp_path / "failures",
            "stub": stub_none,
            "agent_runtime_log_pointer": runtime_log,
        }
    ]

    events = [
        json.loads(line)
        for line in (tmp_path / "logs" / "llm" / "enricher.events.jsonl")
        .read_text()
        .splitlines()
        if line
    ]
    malformed_events = [e for e in events if e.get("event") == "classify_malformed"]
    assert malformed_events == [
        {
            "ts": malformed_events[0]["ts"],
            "source": "test",
            "url": stub_none.url,
            "module": "llm_enricher",
            "event": "classify_malformed",
            "error": "malformed classifier verdict",
        }
    ]


def test_enrich_batch_routes_match_reject_none_independently(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    """Matches write card store + mark_matched; rejections expose a rejected outcome; None untouched."""
    extractor = MagicMock()
    extractor.classify_relevance.return_value = [
        RelevanceVerdict(
            matches=True,
            header="ML Engineer\nAcme · Berlin · remote\n2024-06-01",
            summary="Good role.",
        ),
        RelevanceVerdict(matches=False),
        None,
    ]

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
    extractor.classify_relevance.return_value = [
        RelevanceVerdict(matches=True, header=stale_header, summary="Old."),
        RelevanceVerdict(matches=True, header=fresh_header, summary="Fresh."),
    ]

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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error_msg = "batch classify failed"
    stash_calls = _record_exception_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorMalformedError(error_msg)

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub1 = PositionStub(url="https://example.com/job/a", title="Job A", source="src")
    stub2 = PositionStub(url="https://example.com/job/b", title="Job B", source="src")

    result = enricher.enrich([(1, stub1, "body a"), (2, stub2, "body b")])

    assert [item.state for item in result.items] == ["retryable", "retryable"]
    assert [call["stub"] for call in stash_calls] == [stub1, stub2]


def test_enrich_batch_malformed_exception_stashes_each_listing_identity(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stash_calls = _record_exception_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorBatchMalformedError(
        "batch response could not be parsed"
    )
    extractor.last_classify_log_path = _runtime_log_path(tmp_path)

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub1 = PositionStub(
        url="https://example.com/job/a",
        title="Job A",
        source="src",
    )
    stub2 = PositionStub(
        url="https://example.com/job/b",
        title="Job B",
        source="src",
    )

    result = enricher.enrich([(1, stub1, "body a"), (2, stub2, "body b")])

    assert [item.state for item in result.items] == ["retryable", "retryable"]
    assert len(stash_calls) == 2
    assert stash_calls[0]["filesystem_root"] == tmp_path / "failures"
    assert stash_calls[0]["stub"] == stub1
    assert stash_calls[0]["agent_runtime_log_pointer"] == _runtime_log_path(tmp_path)
    assert stash_calls[0]["raw_description"] == "body a"
    _assert_stashed_error(
        stash_calls[0]["error"],
        ExtractorBatchMalformedError,
        "batch response could not be parsed",
    )
    assert stash_calls[1]["filesystem_root"] == tmp_path / "failures"
    assert stash_calls[1]["stub"] == stub2
    assert stash_calls[1]["agent_runtime_log_pointer"] == _runtime_log_path(tmp_path)
    assert stash_calls[1]["raw_description"] == "body b"
    _assert_stashed_error(
        stash_calls[1]["error"],
        ExtractorBatchMalformedError,
        "batch response could not be parsed",
    )


@pytest.mark.parametrize(
    "raw_verdicts",
    [
        pytest.param([None], id="short"),
        pytest.param(
            [
                RelevanceVerdict(matches=False),
                RelevanceVerdict(matches=False),
                RelevanceVerdict(matches=False),
            ],
            id="long",
        ),
    ],
)
def test_enrich_malformed_batch_length_mismatch_keeps_every_listing_retryable(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
    monkeypatch: pytest.MonkeyPatch,
    raw_verdicts: list[RelevanceVerdict | None],
) -> None:
    runtime_log = _runtime_log_path(tmp_path)
    stash_calls = _record_exception_stash_calls(monkeypatch)
    extractor = MagicMock()
    extractor.classify_relevance.return_value = raw_verdicts
    extractor.last_classify_log_path = runtime_log

    enricher, dedup = _make_enricher_with_dedup(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub_a = PositionStub(
        url="https://example.com/job/short-a",
        title="Job A",
        source="src",
    )
    stub_b = PositionStub(
        url="https://example.com/job/short-b",
        title="Job B",
        source="src",
    )

    with dedup.run_scope():
        dedup.is_seen(stub_a)
        dedup.is_seen(stub_b)
        result = enricher.enrich([(1, stub_a, "body a"), (2, stub_b, "body b")])

    assert [item.state for item in result.items] == ["retryable", "retryable"]
    assert result.matched_listings == []
    assert load_card_store(tmp_path / "extracts.json").get(1) is None
    assert load_card_store(tmp_path / "extracts.json").get(2) is None

    seen_path = tmp_path / ".seen.json"
    assert not seen_path.exists()
    assert len(stash_calls) == 2
    assert [call["stub"] for call in stash_calls] == [stub_a, stub_b]
    assert all(call["agent_runtime_log_pointer"] == runtime_log for call in stash_calls)
    assert all(call["raw_description"] in {"body a", "body b"} for call in stash_calls)
    assert all(
        isinstance(call["error"], ExtractorBatchMalformedError) for call in stash_calls
    )

    events_file = tmp_path / "logs" / "llm" / "enricher.events.jsonl"
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    malformed_events = [e for e in events if e.get("event") == "classify_malformed"]
    assert len(malformed_events) == 1


def test_enricher_fatal_provider_failure_propagates(
    tmp_path: Path,
    run_log: RunLog,
    run_metrics: RunMetrics,
) -> None:
    body = "Software Engineer role."
    extractor = MagicMock()
    extractor.classify_relevance.side_effect = ExtractorUnreachableError(
        "provider unreachable"
    )

    enricher = _make_enricher(
        extractor=extractor, tmp_path=tmp_path, run_log=run_log, run_metrics=run_metrics
    )
    stub = PositionStub(
        url="https://example.com/job/provider",
        title="Software Engineer",
        source="test_src",
    )

    with pytest.raises(ExtractorUnreachableError, match="provider unreachable"):
        enricher.enrich([(99, stub, body)])
