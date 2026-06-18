"""Tests for ClaudeExtractor v2 call shapes - classify_relevance and judge_top_n."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from application_pipeline import ClassifyItem, Config, SourceEntry
from application_pipeline.llm import (
    ClaudeCliInvoker,
    ClaudeExtractor,
    ClaudeMalformedEnvelopeError,
    ClaudeResponse,
    ClaudeUsage,
    ExtractorBatchMalformedError,
    ExtractorMalformedJSONError,
)
from application_pipeline.llm.types import (
    JudgeCandidate,
    MatchVerdict,
    RelevanceVerdict,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.prompts import (
    CLASSIFY_RELEVANCE_SLOTS,
    JUDGE_TOP_N_SLOTS,
    PromptTemplate,
    Prompts,
)
# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


def _config() -> Config:
    return Config(
        sources=[SourceEntry(parser_type="bundesagentur")],
        locations=["Hamburg"],
        claude_cli_path="claude",
    )


def _prompts() -> Prompts:
    return Prompts(
        classify_relevance=PromptTemplate(
            "v2 {LISTINGS}",
            CLASSIFY_RELEVANCE_SLOTS,
        ),
        judge_top_n=PromptTemplate("v2 {CANDIDATES}", JUDGE_TOP_N_SLOTS),
    )


def _usage() -> ClaudeUsage:
    return ClaudeUsage(input_tokens=100, output_tokens=20, cache_read_tokens=0)


def _fake_invoker(response: ClaudeResponse) -> MagicMock:
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.return_value = response
    return invoker


def _classify_response(verdict: object) -> ClaudeResponse:
    return ClaudeResponse(
        raw_response=f'<verdict id="1">{json.dumps(verdict)}</verdict>',
        usage=_usage(),
        cost_usd=0.001,
        duration_s=0.5,
        session_id="s",
    )


def _judge_response(verdicts: list[dict[str, int]]) -> ClaudeResponse:
    return ClaudeResponse(
        raw_response=f"<verdicts>{json.dumps(verdicts)}</verdicts>",
        usage=_usage(),
        cost_usd=0.003,
        duration_s=2.0,
        session_id="s-judge",
    )


def _item(**kwargs: object) -> ClassifyItem:
    defaults: dict[str, object] = dict(
        title="Senior Python Engineer", raw_description="Python ML role"
    )
    defaults.update(kwargs)
    return ClassifyItem(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# classify_relevance: matched happy path
# ---------------------------------------------------------------------------


def test_classify_relevance_matched_returns_header_and_summary(
    run_log: RunLog,
) -> None:
    invoker = _fake_invoker(
        _classify_response(
            {
                "matches": True,
                "header": "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01",
                "summary": "Great role for ML engineers.",
            }
        )
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    results, usage = extractor.classify_relevance(
        [_item(company="Acme", location="Hamburg")]
    )
    result = results[0]
    assert isinstance(result, RelevanceVerdict)
    assert result.matches is True
    assert (
        result.header == "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01"
    )
    assert result.summary == "Great role for ML engineers."
    assert usage.input_tokens == 100
    assert usage.cost_usd == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# classify_relevance: out-of-domain
# ---------------------------------------------------------------------------


def test_classify_relevance_out_of_domain_returns_none_header_and_summary(
    run_log: RunLog,
) -> None:
    invoker = _fake_invoker(_classify_response({"matches": False}))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    results, _ = extractor.classify_relevance([_item()])
    result = results[0]
    assert isinstance(result, RelevanceVerdict)
    assert result.matches is False
    assert result.header is None
    assert result.summary is None


# ---------------------------------------------------------------------------
# classify_relevance: malformed responses → None (batch protocol)
# ---------------------------------------------------------------------------


def test_classify_relevance_matched_missing_header_returns_none(
    run_log: RunLog,
) -> None:
    invoker = _fake_invoker(_classify_response({"matches": True, "summary": "ok"}))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    results, _ = extractor.classify_relevance([_item()])
    assert results[0] is None


def test_classify_relevance_matched_missing_summary_returns_none(
    run_log: RunLog,
) -> None:
    invoker = _fake_invoker(
        _classify_response({"matches": True, "header": "some header"})
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    results, _ = extractor.classify_relevance([_item()])
    assert results[0] is None


def test_classify_relevance_matched_empty_header_returns_none(
    run_log: RunLog,
) -> None:
    invoker = _fake_invoker(
        _classify_response({"matches": True, "header": "", "summary": "ok"})
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    results, _ = extractor.classify_relevance([_item()])
    assert results[0] is None


# ---------------------------------------------------------------------------
# classify_relevance: transport errors still raise
# ---------------------------------------------------------------------------


def test_classify_relevance_envelope_malformed_attaches_prompt_and_none_raw_response(
    run_log: RunLog,
) -> None:
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeMalformedEnvelopeError(
        "envelope JSON unparseable",
        returncode=0,
        stdout="garbage",
        stderr="",
        envelope=None,
        envelope_error_class="envelope_not_json",
    )
    extractor = ClaudeExtractor(
        _config(), _prompts(), run_log=run_log, _invoker=invoker
    )
    with pytest.raises(ExtractorMalformedJSONError) as excinfo:
        extractor.classify_relevance([_item()])
    transcripts = _read_transcripts(run_log, "llm_classify_relevance")
    assert excinfo.value.prompt == transcripts[0]["prompt"]
    assert excinfo.value.raw_response is None
    assert excinfo.value.returncode == 0
    assert excinfo.value.stderr == ""
    assert transcripts[0]["status"] == "malformed_envelope"


# ---------------------------------------------------------------------------
# classify_relevance: prompt receives pre-fill fields
# ---------------------------------------------------------------------------


def test_classify_relevance_prompt_includes_company_and_location(
    run_log: RunLog,
) -> None:
    invoker = _fake_invoker(
        _classify_response({"matches": True, "header": "h", "summary": "s"})
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    extractor.classify_relevance([_item(company="TestCorp", location="Berlin")])
    prompt_sent = _read_transcripts(run_log, "llm_classify_relevance")[0]["prompt"]
    assert "TestCorp" in prompt_sent
    assert "Berlin" in prompt_sent


def test_classify_relevance_legacy_in_domain_field_returns_none(
    run_log: RunLog,
) -> None:
    invoker = _fake_invoker(
        _classify_response({"in_domain": True, "header": "h", "summary": "s"})
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    results, _ = extractor.classify_relevance([_item()])
    assert results[0] is None


# ---------------------------------------------------------------------------
# judge_top_n: happy path
# ---------------------------------------------------------------------------


def _make_candidates(n: int) -> list[JudgeCandidate]:
    return [
        JudgeCandidate(id=i, header=f"Title {i}\nCo", summary=f"Summary {i}")
        for i in range(n)
    ]


def test_judge_top_n_returns_match_verdict_with_id_and_rank(
    run_log: RunLog,
) -> None:
    candidates = _make_candidates(5)
    verdicts_raw = [{"id": c.id, "rank": i + 1} for i, c in enumerate(candidates)]
    invoker = _fake_invoker(_judge_response(verdicts_raw))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    results, usage = extractor.judge_top_n(candidates)
    assert len(results) == 5
    assert all(isinstance(v, MatchVerdict) for v in results)
    assert {v.rank for v in results} == {1, 2, 3, 4, 5}
    assert all(v.id in {c.id for c in candidates} for v in results)
    assert usage.cost_usd == pytest.approx(0.003)


def test_judge_top_n_empty_candidates_returns_empty_list(
    run_log: RunLog,
) -> None:
    invoker = MagicMock(spec=ClaudeCliInvoker)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    results, usage = extractor.judge_top_n([])
    assert results == []
    assert usage.cost_usd == pytest.approx(0.0)
    assert _read_transcripts(run_log, "llm_judge_match") == []
    assert _read_events(run_log, "llm_judge_match") == []


def test_judge_top_n_candidates_appear_in_prompt(
    run_log: RunLog,
) -> None:
    candidates = _make_candidates(2)
    verdicts_raw = [{"id": c.id, "rank": i + 1} for i, c in enumerate(candidates)]
    invoker = _fake_invoker(_judge_response(verdicts_raw))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    extractor.judge_top_n(candidates)
    prompt_sent = _read_transcripts(run_log, "llm_judge_match")[0]["prompt"]
    assert "[Candidate id=0]" in prompt_sent
    assert "[Candidate id=1]" in prompt_sent


def test_judge_top_n_coerces_string_id_to_int(run_log: RunLog) -> None:
    candidates = _make_candidates(3)
    invoker = _fake_invoker(
        ClaudeResponse(
            raw_response='<verdicts>[{"id": "0", "rank": 1}]</verdicts>',
            usage=_usage(),
            cost_usd=0.003,
            duration_s=2.0,
            session_id="s-judge",
        )
    )
    extractor = ClaudeExtractor(
        _config(), _prompts(), run_log=run_log, _invoker=invoker
    )
    verdicts, _ = extractor.judge_top_n(candidates)
    assert len(verdicts) == 1
    assert verdicts[0].id == 0
    assert verdicts[0].rank == 1


def test_judge_top_n_rejects_non_numeric_string_verdict_id(run_log: RunLog) -> None:
    candidates = _make_candidates(3)
    invoker = _fake_invoker(
        ClaudeResponse(
            raw_response='<verdicts>[{"id": "abc", "rank": 1}]</verdicts>',
            usage=_usage(),
            cost_usd=0.003,
            duration_s=2.0,
            session_id="s-judge",
        )
    )
    extractor = ClaudeExtractor(
        _config(), _prompts(), run_log=run_log, _invoker=invoker
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.judge_top_n(candidates)


def _read_transcripts(run_log: RunLog, component_id: str) -> list[dict]:  # type: ignore[type-arg]
    path = (
        run_log.logs_dir
        / "llm"
        / f"{component_id.removeprefix('llm_')}.transcripts.jsonl"
    )
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _read_events(run_log: RunLog, component_id: str) -> list[dict]:  # type: ignore[type-arg]
    path = (
        run_log.logs_dir / "llm" / f"{component_id.removeprefix('llm_')}.events.jsonl"
    )
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# ---------------------------------------------------------------------------
# classify_relevance: batch protocol — single call for N items
# ---------------------------------------------------------------------------


def _batch_prompts() -> Prompts:
    """Prompts using the new LISTINGS slot for batch classify."""
    return Prompts(
        classify_relevance=PromptTemplate(
            "batch {LISTINGS}",
            frozenset({"LISTINGS"}),
        ),
        judge_top_n=PromptTemplate("v2 {CANDIDATES}", JUDGE_TOP_N_SLOTS),
    )


def _batch_classify_response(
    id_verdict_pairs: list[tuple[int, object]],
) -> ClaudeResponse:
    parts = [
        f'<verdict id="{id_}">{json.dumps(verdict)}</verdict>'
        for id_, verdict in id_verdict_pairs
    ]
    return ClaudeResponse(
        raw_response="\n".join(parts),
        usage=_usage(),
        cost_usd=0.002,
        duration_s=1.0,
        session_id="s-batch",
    )


def test_classify_relevance_batch_prompt_includes_sequential_ids(
    run_log: RunLog,
) -> None:
    items = [_item(title=f"Job {i + 1}") for i in range(3)]
    response = _batch_classify_response(
        [(1, {"matches": False}), (2, {"matches": False}), (3, {"matches": False})]
    )
    invoker = _fake_invoker(response)
    extractor = ClaudeExtractor(
        _config(), _batch_prompts(), run_log=run_log, _invoker=invoker
    )
    extractor.classify_relevance(items)
    prompt_sent = _read_transcripts(run_log, "llm_classify_relevance")[0]["prompt"]
    assert "id=1" in prompt_sent
    assert "id=2" in prompt_sent
    assert "id=3" in prompt_sent


def test_classify_relevance_out_of_order_verdicts_map_to_correct_positions(
    run_log: RunLog,
) -> None:
    items = [_item(title=f"Job {i + 1}") for i in range(3)]
    # verdicts arrive as id=3, id=1, id=2
    response = _batch_classify_response(
        [
            (3, {"matches": True, "header": "h3", "summary": "s3"}),
            (1, {"matches": True, "header": "h1", "summary": "s1"}),
            (2, {"matches": False}),
        ]
    )
    invoker = _fake_invoker(response)
    extractor = ClaudeExtractor(
        _config(), _batch_prompts(), run_log=run_log, _invoker=invoker
    )
    results, _ = extractor.classify_relevance(items)
    assert len(results) == 3
    assert results[0] is not None and results[0].header == "h1"
    assert results[1] is not None and results[1].matches is False
    assert results[2] is not None and results[2].header == "h3"


def test_classify_relevance_missing_verdict_tag_produces_none(
    run_log: RunLog,
) -> None:
    items = [_item(title="Job 1"), _item(title="Job 2")]
    # only id=1 present; id=2 missing
    response = _batch_classify_response([(1, {"matches": False})])
    invoker = _fake_invoker(response)
    extractor = ClaudeExtractor(
        _config(), _batch_prompts(), run_log=run_log, _invoker=invoker
    )
    results, _ = extractor.classify_relevance(items)
    assert results[0] is not None and results[0].matches is False
    assert results[1] is None


def test_classify_relevance_malformed_verdict_json_produces_none(
    run_log: RunLog,
) -> None:
    items = [_item(title="Job 1"), _item(title="Job 2")]
    response = ClaudeResponse(
        raw_response='<verdict id="1">{"matches": false}</verdict>'
        '<verdict id="2">not valid json</verdict>',
        usage=_usage(),
        cost_usd=0.001,
        duration_s=0.5,
        session_id="s",
    )
    invoker = _fake_invoker(response)
    extractor = ClaudeExtractor(
        _config(), _batch_prompts(), run_log=run_log, _invoker=invoker
    )
    results, _ = extractor.classify_relevance(items)
    assert results[0] is not None and results[0].matches is False
    assert results[1] is None


def test_classify_relevance_all_verdicts_missing_returns_all_none_no_error(
    run_log: RunLog,
) -> None:
    items = [_item(), _item()]
    response = ClaudeResponse(
        raw_response="no verdict tags here",
        usage=_usage(),
        cost_usd=0.001,
        duration_s=0.5,
        session_id="s",
    )
    invoker = _fake_invoker(response)
    extractor = ClaudeExtractor(
        _config(), _batch_prompts(), run_log=run_log, _invoker=invoker
    )
    results, _ = extractor.classify_relevance(items)
    assert results == [None, None]


def test_classify_relevance_usage_reflects_single_call_tokens(
    run_log: RunLog,
) -> None:
    items = [_item() for _ in range(3)]
    response = ClaudeResponse(
        raw_response=(
            '<verdict id="1">{"matches": false}</verdict>'
            '<verdict id="2">{"matches": false}</verdict>'
            '<verdict id="3">{"matches": false}</verdict>'
        ),
        usage=ClaudeUsage(input_tokens=500, output_tokens=60, cache_read_tokens=200),
        cost_usd=0.01,
        duration_s=2.5,
        session_id="s",
    )
    invoker = _fake_invoker(response)
    extractor = ClaudeExtractor(
        _config(), _batch_prompts(), run_log=run_log, _invoker=invoker
    )
    _, usage = extractor.classify_relevance(items)
    assert usage.input_tokens == 500
    assert usage.output_tokens == 60
    assert usage.cache_read_tokens == 200
    assert usage.cost_usd == pytest.approx(0.01)


def test_classify_relevance_batch_logs_the_full_batch_prompt_and_response(
    run_log: RunLog,
) -> None:
    items = [_item(title=f"Job {i + 1}") for i in range(3)]
    response = _batch_classify_response(
        [(1, {"matches": False}), (2, {"matches": False}), (3, {"matches": False})]
    )
    invoker = _fake_invoker(response)
    extractor = ClaudeExtractor(
        _config(), _batch_prompts(), run_log=run_log, _invoker=invoker
    )
    extractor.classify_relevance(items)
    transcript = _read_transcripts(run_log, "llm_classify_relevance")[-1]
    assert "Job 1" in transcript["prompt"]
    assert "Job 2" in transcript["prompt"]
    assert "Job 3" in transcript["prompt"]
    assert transcript["raw_response"] == response.raw_response
