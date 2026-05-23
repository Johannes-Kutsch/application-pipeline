"""Tests for ClaudeExtractor v2 call shapes â€” classify_relevance and judge_top_n."""

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
    ExtractorMalformedError,
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
            "v2 {LISTING_BULLETS} {RAW_DESCRIPTION}",
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
        raw_response=f"<verdict>{json.dumps(verdict)}</verdict>",
        usage=_usage(),
        cost_usd=0.001,
        duration_s=0.5,
        session_id="s",
    )


def _judge_response(verdicts: list[dict[str, object]]) -> ClaudeResponse:
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
    result, usage = extractor.classify_relevance(
        _item(company="Acme", location="Hamburg")
    )
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
    result, _ = extractor.classify_relevance(_item())
    assert isinstance(result, RelevanceVerdict)
    assert result.matches is False
    assert result.header is None
    assert result.summary is None


# ---------------------------------------------------------------------------
# classify_relevance: malformed responses
# ---------------------------------------------------------------------------


def test_classify_relevance_matched_missing_header_raises_malformed(
    run_log: RunLog,
) -> None:
    invoker = _fake_invoker(_classify_response({"matches": True, "summary": "ok"}))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        _invoker=invoker,
    )
    with pytest.raises(ExtractorMalformedError):
        extractor.classify_relevance(_item())


def test_classify_relevance_matched_missing_summary_raises_malformed(
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
    with pytest.raises(ExtractorMalformedError):
        extractor.classify_relevance(_item())


def test_classify_relevance_matched_empty_header_raises_malformed(
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
    with pytest.raises(ExtractorMalformedError):
        extractor.classify_relevance(_item())


# ---------------------------------------------------------------------------
# classify_relevance: malformed exceptions carry prompt and raw_response
# ---------------------------------------------------------------------------


def test_classify_relevance_schema_mismatch_attaches_prompt_and_raw_response(
    run_log: RunLog,
) -> None:
    response = _classify_response({"matches": True, "summary": "ok"})
    invoker = _fake_invoker(response)
    extractor = ClaudeExtractor(
        _config(), _prompts(), run_log=run_log, _invoker=invoker
    )
    with pytest.raises(ExtractorMalformedError) as excinfo:
        extractor.classify_relevance(_item())
    sent_prompt = invoker.call.call_args.args[0]
    assert excinfo.value.prompt == sent_prompt
    assert excinfo.value.raw_response == response.raw_response


def test_classify_relevance_protocol_error_attaches_prompt_and_raw_response(
    run_log: RunLog,
) -> None:
    response = ClaudeResponse(
        raw_response="no verdict block here",
        usage=_usage(),
        cost_usd=0.001,
        duration_s=0.1,
        session_id="s",
    )
    invoker = _fake_invoker(response)
    extractor = ClaudeExtractor(
        _config(), _prompts(), run_log=run_log, _invoker=invoker
    )
    with pytest.raises(ExtractorMalformedError) as excinfo:
        extractor.classify_relevance(_item())
    sent_prompt = invoker.call.call_args.args[0]
    assert excinfo.value.prompt == sent_prompt
    assert excinfo.value.raw_response == response.raw_response


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
        extractor.classify_relevance(_item())
    sent_prompt = invoker.call.call_args.args[0]
    assert excinfo.value.prompt == sent_prompt
    assert excinfo.value.raw_response is None
    assert excinfo.value.returncode == 0
    assert excinfo.value.stderr == ""


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
    extractor.classify_relevance(_item(company="TestCorp", location="Berlin"))
    prompt_sent = invoker.call.call_args.args[0]
    assert "TestCorp" in prompt_sent
    assert "Berlin" in prompt_sent


def test_classify_relevance_legacy_in_domain_field_raises_malformed(
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
    with pytest.raises(ExtractorMalformedError):
        extractor.classify_relevance(_item())


# ---------------------------------------------------------------------------
# judge_top_n: happy path
# ---------------------------------------------------------------------------


def _make_candidates(n: int) -> list[JudgeCandidate]:
    return [
        JudgeCandidate(id=f"cand-{i}", header=f"Title {i}\nCo", summary=f"Summary {i}")
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
    invoker.call.assert_not_called()


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
    prompt_sent = invoker.call.call_args.args[0]
    assert "cand-0" in prompt_sent
    assert "cand-1" in prompt_sent
