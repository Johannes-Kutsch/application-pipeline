"""Tests for ClaudeExtractor — classify_relevance (solo call) + judge_top_n via Claude CLI."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from application_pipeline import (
    ClassifyItem,
    Config,
    LLMExtractor,
    RelevanceVerdict,
    SourceEntry,
    StructuredExtract,
)
from application_pipeline.llm import (
    ClaudeExtractor,
    ClaudeCliInvoker,
    ClaudeResponse,
    ClaudeUsage,
    ExtractorMalformedError,
    ExtractorMalformedJSONError,
    ExtractorUnreachableError,
    JudgeCandidate,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.llm.claude_cli import (
    ClaudeCliError,
    ClaudeMalformedEnvelopeError,
    ClaudeUsageLimitError,
)
from application_pipeline.prompts import (
    CLASSIFY_RELEVANCE_SLOTS,
    JUDGE_TOP_N_SYSTEM_SLOTS,
    JUDGE_TOP_N_USER_SLOTS,
    PromptTemplate,
    Prompts,
    SplitPromptTemplate,
)
from application_pipeline.search_terms.types import SearchTerms


_SEARCH_TERMS = SearchTerms(keywords=("python",), skills=(), negative_keywords=())


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


def _config(**kwargs: object) -> Config:
    defaults: dict[str, object] = dict(
        sources=[SourceEntry(parser_type="bundesagentur")],
        locations=["Hamburg"],
        claude_cli_path="claude",
    )
    defaults.update(kwargs)
    return Config(**defaults)  # type: ignore[arg-type]


def _prompts(
    classify: str = "{TITLE} {RAW_DESCRIPTION}",
    judge_top_n: str = "{candidates}",
) -> Prompts:
    return Prompts(
        classify_relevance=SplitPromptTemplate(
            system=PromptTemplate("system", frozenset()),
            user=PromptTemplate(classify, CLASSIFY_RELEVANCE_SLOTS),
        ),
        judge_top_n=SplitPromptTemplate(
            system=PromptTemplate("{skills}", JUDGE_TOP_N_SYSTEM_SLOTS),
            user=PromptTemplate(judge_top_n, JUDGE_TOP_N_USER_SLOTS),
        ),
    )


def _usage() -> ClaudeUsage:
    return ClaudeUsage(input_tokens=100, output_tokens=20, cache_read_tokens=0)


def _classify_raw(verdict: object) -> str:
    return f"<verdict>{json.dumps(verdict)}</verdict>"


def _classify_response(verdict: object) -> ClaudeResponse:
    return ClaudeResponse(
        raw_response=_classify_raw(verdict),
        usage=_usage(),
        cost_usd=0.001,
        duration_s=0.5,
        session_id="s",
    )


_DEFAULT_EXTRACT: dict[str, object] = {
    "seniority": None,
    "work_model": None,
    "contract_type": None,
    "key_skills": [],
    "key_responsibilities": [],
    "must_have_requirements": [],
    "notable_caveats": "",
}


def _in_domain_response() -> ClaudeResponse:
    return _classify_response({"in_domain": True, "extract": _DEFAULT_EXTRACT})


def _out_of_domain_response() -> ClaudeResponse:
    return _classify_response({"in_domain": False})


def _fake_invoker(response: ClaudeResponse) -> MagicMock:
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.return_value = response
    return invoker


def _item(
    title: str = "Software Engineer", raw_description: str = "Python role"
) -> ClassifyItem:
    return ClassifyItem(title=title, raw_description=raw_description)


# ---------------------------------------------------------------------------
# classify_relevance: happy path
# ---------------------------------------------------------------------------


def test_classify_relevance_returns_in_domain_true(run_log: RunLog) -> None:
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_in_domain_response()),
    )
    result, usage = extractor.classify_relevance(_item())
    assert isinstance(result, RelevanceVerdict)
    assert result.in_domain is True
    assert usage.input_tokens == 100
    assert usage.cost_usd == pytest.approx(0.001)


def test_classify_relevance_returns_in_domain_false(run_log: RunLog) -> None:
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_out_of_domain_response()),
    )
    result, _ = extractor.classify_relevance(_item())
    assert result.in_domain is False
    assert result.extract is None


def test_classify_relevance_prompt_contains_title_and_description(
    run_log: RunLog,
) -> None:
    invoker = _fake_invoker(_in_domain_response())
    extractor = ClaudeExtractor(
        _config(),
        _prompts(classify="T={TITLE} D={RAW_DESCRIPTION}"),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )
    item = _item(title="MY_TITLE", raw_description="MY_DESC")
    extractor.classify_relevance(item)
    prompt_sent = invoker.call.call_args.args[0]
    assert "MY_TITLE" in prompt_sent
    assert "MY_DESC" in prompt_sent


def test_classify_relevance_sends_combined_prompt_via_stdin_no_system_prompt(
    run_log: RunLog,
) -> None:
    invoker = _fake_invoker(_in_domain_response())
    prompts = Prompts(
        classify_relevance=SplitPromptTemplate(
            system=PromptTemplate("SYS_BODY", frozenset()),
            user=PromptTemplate("{TITLE}|{RAW_DESCRIPTION}", CLASSIFY_RELEVANCE_SLOTS),
        ),
        judge_top_n=SplitPromptTemplate(
            system=PromptTemplate("{skills}", JUDGE_TOP_N_SYSTEM_SLOTS),
            user=PromptTemplate("{candidates}", JUDGE_TOP_N_USER_SLOTS),
        ),
    )
    extractor = ClaudeExtractor(
        _config(),
        prompts,
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )
    extractor.classify_relevance(_item(title="T", raw_description="D"))
    kwargs = invoker.call.call_args.kwargs
    stdin_body = invoker.call.call_args.args[0]
    # Classifier must NOT send a system_prompt flag
    assert not kwargs.get("system_prompt"), (
        "system_prompt must be absent/empty for classifier"
    )
    # stdin must carry the full combined prompt (system half + blank line + user half)
    assert stdin_body == "SYS_BODY\n\nT|D"


# ---------------------------------------------------------------------------
# classify_relevance: structured extract on in-domain verdicts
# ---------------------------------------------------------------------------

_EXTRACT_PAYLOAD: dict[str, object] = {
    "seniority": "senior",
    "work_model": "remote",
    "contract_type": "permanent",
    "key_skills": ["Python", "SQL"],
    "key_responsibilities": ["Build pipelines"],
    "must_have_requirements": ["5+ years Python"],
    "notable_caveats": "",
}


def test_classify_in_domain_verdict_carries_structured_extract(
    run_log: RunLog,
) -> None:
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(
            _classify_response({"in_domain": True, "extract": _EXTRACT_PAYLOAD})
        ),
    )
    result, _ = extractor.classify_relevance(_item("Data Engineer", "Python+SQL role"))
    assert result.in_domain is True
    assert result.extract is not None
    assert result.extract.seniority == "senior"
    assert result.extract.work_model == "remote"
    assert result.extract.contract_type == "permanent"
    assert result.extract.key_skills == ["Python", "SQL"]
    assert result.extract.key_responsibilities == ["Build pipelines"]
    assert result.extract.must_have_requirements == ["5+ years Python"]
    assert result.extract.notable_caveats == ""


def test_classify_out_of_domain_verdict_has_extract_none(run_log: RunLog) -> None:
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_out_of_domain_response()),
    )
    result, _ = extractor.classify_relevance(_item("Cashier", "Shop job"))
    assert result.in_domain is False
    assert result.extract is None


def test_classify_in_domain_missing_extract_raises_malformed(
    run_log: RunLog,
) -> None:
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_classify_response({"in_domain": True})),
    )
    with pytest.raises(ExtractorMalformedError):
        extractor.classify_relevance(_item())


def test_classify_in_domain_malformed_extract_raises_malformed(
    run_log: RunLog,
) -> None:
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(
            _classify_response({"in_domain": True, "extract": "not-a-dict"})
        ),
    )
    with pytest.raises(ExtractorMalformedError):
        extractor.classify_relevance(_item())


def test_classify_in_domain_extract_missing_required_field_raises_malformed(
    run_log: RunLog,
) -> None:
    incomplete_extract = {"seniority": "senior", "work_model": "remote"}
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(
            _classify_response({"in_domain": True, "extract": incomplete_extract})
        ),
    )
    with pytest.raises(ExtractorMalformedError):
        extractor.classify_relevance(_item())


def test_classify_non_dict_response_raises_malformed(run_log: RunLog) -> None:
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_classify_response([{"in_domain": True}])),
    )
    with pytest.raises(ExtractorMalformedError):
        extractor.classify_relevance(_item())


# ---------------------------------------------------------------------------
# classify_relevance: transcript recording
# ---------------------------------------------------------------------------


def test_classify_relevance_records_event_row(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_in_domain_response()),
    )

    extractor.classify_relevance(_item())

    events_file = tmp_path / "llm_classify_relevance.events.jsonl"
    assert events_file.exists()
    entry = json.loads(events_file.read_text(encoding="utf-8").strip())
    assert entry["event"] == "classify_relevance"
    assert "cost_usd" in entry
    assert "duration_s" in entry
    assert "batch_size" not in entry


def test_classify_relevance_records_transcript(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_in_domain_response()),
    )

    extractor.classify_relevance(_item())

    transcript_file = tmp_path / "llm_classify_relevance.transcripts.jsonl"
    assert transcript_file.exists()
    entry = json.loads(transcript_file.read_text(encoding="utf-8").strip())
    assert entry["call"] == "classify_relevance"
    assert "prompt" in entry
    assert "system_prompt" not in entry
    assert "raw_response" in entry
    assert "usage" in entry
    assert "cost_usd" in entry
    assert "duration_s" in entry
    assert "batch_size" not in entry


# ---------------------------------------------------------------------------
# claude_extractor.* files are never created
# ---------------------------------------------------------------------------


def test_successful_classify_does_not_write_claude_extractor_files(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_in_domain_response()),
    )
    extractor.classify_relevance(_item())

    assert not (tmp_path / "claude_extractor.events.jsonl").exists()
    assert not (tmp_path / "claude_extractor.transcripts.jsonl").exists()


# ---------------------------------------------------------------------------
# Failure-path: does not pollute the success transcript file
# ---------------------------------------------------------------------------


def test_classify_failure_transcript_does_not_write_to_extractor_file(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeCliError(
        "exit 1",
        returncode=1,
        stdout="",
        stderr="",
        envelope=None,
        envelope_error_class="cli_nonzero_exit",
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )

    with pytest.raises(ExtractorUnreachableError):
        extractor.classify_relevance(_item())

    assert not (tmp_path / "claude_extractor.transcripts.jsonl").exists()


def test_classify_failure_transcript_has_no_system_prompt_field(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeCliError(
        "exit 1",
        returncode=1,
        stdout="",
        stderr="",
        envelope=None,
        envelope_error_class="cli_nonzero_exit",
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )

    with pytest.raises(ExtractorUnreachableError):
        extractor.classify_relevance(_item())

    transcript_file = tmp_path / "llm_classify_relevance.transcripts.jsonl"
    entry = json.loads(transcript_file.read_text(encoding="utf-8").strip())
    assert "system_prompt" not in entry
    assert "prompt" in entry


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_claude_extractor_is_llm_extractor(run_log: RunLog) -> None:
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=MagicMock(spec=ClaudeCliInvoker),
    )
    assert isinstance(extractor, LLMExtractor)


# ---------------------------------------------------------------------------
# Model / effort pinning
# ---------------------------------------------------------------------------


def test_classify_relevance_passes_haiku_model_to_invoker(run_log: RunLog) -> None:
    invoker = _fake_invoker(_in_domain_response())
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )
    extractor.classify_relevance(_item())
    call_kwargs = invoker.call.call_args.kwargs
    assert call_kwargs["model"] == "haiku"
    assert call_kwargs.get("effort", "") == ""


# ---------------------------------------------------------------------------
# Slot drift tests
# ---------------------------------------------------------------------------


def test_classify_slots_match_inventory(run_log: RunLog) -> None:
    invoker = _fake_invoker(_in_domain_response())
    extractor = ClaudeExtractor(
        _config(),
        _prompts(classify="content={TITLE} {RAW_DESCRIPTION}"),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )
    extractor.classify_relevance(_item(title="MY_TITLE", raw_description="MY_DESC"))
    prompt_sent = invoker.call.call_args.args[0]
    assert "MY_TITLE" in prompt_sent
    assert "MY_DESC" in prompt_sent


# ---------------------------------------------------------------------------
# Parametrised failure-path tests: CLI error, malformed envelope,
# tag missing, JSON malformed — parameter selects the call site.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invoke,transcript_file,stderr,extra_transcript_assertions",
    [
        pytest.param(
            lambda e: e.classify_relevance(_item()),
            "llm_classify_relevance.transcripts.jsonl",
            "some stderr",
            {"stdout": '{"type":"result","result":"","is_error":false}'},
            id="classify",
        ),
    ],
)
def test_cli_error(
    tmp_path: Path,
    invoke: Callable[[ClaudeExtractor], object],
    transcript_file: str,
    stderr: str,
    extra_transcript_assertions: dict[str, object],
) -> None:
    run_log = RunLog(tmp_path)
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeCliError(
        "empty result",
        returncode=0,
        stdout='{"type":"result","result":"","is_error":false}',
        stderr=stderr,
        envelope={"type": "result", "result": "", "is_error": False},
        envelope_error_class="empty_result",
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )

    with pytest.raises(ExtractorUnreachableError) as exc_info:
        invoke(extractor)

    assert exc_info.value.returncode == 0
    assert exc_info.value.stderr == stderr

    entry = json.loads((tmp_path / transcript_file).read_text(encoding="utf-8"))
    assert entry["status"] == "cli_error"
    assert "prompt" in entry
    assert entry["returncode"] == 0
    assert entry["stderr"] == stderr
    assert entry["envelope_error_class"] == "empty_result"
    for key, val in extra_transcript_assertions.items():
        assert entry[key] == val


@pytest.mark.parametrize(
    "invoke,transcript_file,extra_transcript_assertions",
    [
        pytest.param(
            lambda e: e.classify_relevance(_item()),
            "llm_classify_relevance.transcripts.jsonl",
            {},
            id="classify",
        ),
    ],
)
def test_malformed_envelope(
    tmp_path: Path,
    invoke: Callable[[ClaudeExtractor], object],
    transcript_file: str,
    extra_transcript_assertions: dict[str, object],
) -> None:
    run_log = RunLog(tmp_path)
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeMalformedEnvelopeError(
        "bad json",
        returncode=0,
        stdout="not-json",
        stderr="",
        envelope=None,
        envelope_error_class="envelope_not_json",
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )

    with pytest.raises(ExtractorMalformedJSONError):
        invoke(extractor)

    entry = json.loads((tmp_path / transcript_file).read_text(encoding="utf-8"))
    assert entry["status"] == "malformed_envelope"
    assert entry["envelope"] is None
    for key, val in extra_transcript_assertions.items():
        assert entry[key] == val


@pytest.mark.parametrize(
    "invoke,transcript_file,expected_error_cls",
    [
        pytest.param(
            lambda e: e.classify_relevance(_item()),
            "llm_classify_relevance.transcripts.jsonl",
            ExtractorMalformedError,
            id="classify",
        ),
    ],
)
def test_tag_missing(
    tmp_path: Path,
    invoke: Callable[[ClaudeExtractor], object],
    transcript_file: str,
    expected_error_cls: type,
) -> None:
    run_log = RunLog(tmp_path)
    raw = "no tags here"
    response = ClaudeResponse(
        raw_response=raw, usage=_usage(), cost_usd=0.0, duration_s=0.1, session_id="s"
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(response),
    )

    with pytest.raises(expected_error_cls):
        invoke(extractor)

    entry = json.loads((tmp_path / transcript_file).read_text(encoding="utf-8"))
    assert entry["envelope_error_class"] == "tag_missing"
    assert entry["raw_response"] == raw


@pytest.mark.parametrize(
    "invoke,transcript_file,expected_error_cls,raw",
    [
        pytest.param(
            lambda e: e.classify_relevance(_item()),
            "llm_classify_relevance.transcripts.jsonl",
            ExtractorMalformedError,
            "<verdict>bad json</verdict>",
            id="classify",
        ),
    ],
)
def test_json_malformed(
    tmp_path: Path,
    invoke: Callable[[ClaudeExtractor], object],
    transcript_file: str,
    expected_error_cls: type,
    raw: str,
) -> None:
    run_log = RunLog(tmp_path)
    response = ClaudeResponse(
        raw_response=raw, usage=_usage(), cost_usd=0.0, duration_s=0.1, session_id="s"
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(response),
    )

    with pytest.raises(expected_error_cls):
        invoke(extractor)

    entry = json.loads((tmp_path / transcript_file).read_text(encoding="utf-8"))
    assert entry["envelope_error_class"] == "json_malformed"
    assert entry["raw_response"] == raw


def test_classify_usage_limit_propagates(run_log: RunLog) -> None:
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeUsageLimitError(
        "rate limit", returncode=1, stdout="", stderr="rate limit", envelope=None
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )
    with pytest.raises(ClaudeUsageLimitError):
        extractor.classify_relevance(_item())


# ---------------------------------------------------------------------------
# judge_top_n helpers
# ---------------------------------------------------------------------------

_EMPTY_EXTRACT = StructuredExtract(
    seniority=None,
    work_model=None,
    contract_type=None,
    key_skills=[],
    key_responsibilities=[],
    must_have_requirements=[],
    notable_caveats="",
)


def _judge_candidates(n: int) -> list[JudgeCandidate]:
    return [
        JudgeCandidate(
            id=f"cand-{i}",
            extract=_EMPTY_EXTRACT,
            title=f"Title {i}",
            company=None,
            location=None,
        )
        for i in range(n)
    ]


def _top_n_response(verdicts: list[dict[str, object]]) -> ClaudeResponse:
    return ClaudeResponse(
        raw_response=f"<verdicts>{json.dumps(verdicts)}</verdicts>",
        usage=_usage(),
        cost_usd=0.003,
        duration_s=2.0,
        session_id="sess-top-n",
    )


def _default_top_n_verdicts(
    candidates: list[JudgeCandidate], count: int = 5
) -> list[dict[str, object]]:
    return [
        {
            "id": candidates[i].id,
            "rank": i + 1,
            "matched": [],
            "missing": [],
            "summary": "ok",
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# judge_top_n: happy path
# ---------------------------------------------------------------------------


def test_judge_top_n_with_10_candidates_returns_5_verdicts(run_log: RunLog) -> None:
    candidates = _judge_candidates(10)
    verdicts_raw = _default_top_n_verdicts(candidates, count=5)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_top_n_response(verdicts_raw)),
    )
    results, usage = extractor.judge_top_n(candidates)
    assert len(results) == 5
    assert {v.rank for v in results} == {1, 2, 3, 4, 5}
    assert all(v.id in {c.id for c in candidates} for v in results)
    assert usage.cost_usd == pytest.approx(0.003)


def test_judge_top_n_sends_combined_prompt_via_stdin_no_system_prompt(
    run_log: RunLog,
) -> None:
    candidates = _judge_candidates(2)
    verdicts_raw = _default_top_n_verdicts(candidates, count=2)
    invoker = _fake_invoker(_top_n_response(verdicts_raw))
    prompts = Prompts(
        classify_relevance=SplitPromptTemplate(
            system=PromptTemplate("SYS_BODY", frozenset()),
            user=PromptTemplate("{TITLE}|{RAW_DESCRIPTION}", CLASSIFY_RELEVANCE_SLOTS),
        ),
        judge_top_n=SplitPromptTemplate(
            system=PromptTemplate("JUDGE_SYS {skills}", JUDGE_TOP_N_SYSTEM_SLOTS),
            user=PromptTemplate("{candidates}", JUDGE_TOP_N_USER_SLOTS),
        ),
    )
    search_terms = SearchTerms(
        keywords=("python",), skills=("Python",), negative_keywords=()
    )
    extractor = ClaudeExtractor(
        _config(),
        prompts,
        search_terms=search_terms,
        run_log=run_log,
        _invoker=invoker,
    )
    extractor.judge_top_n(candidates)
    kwargs = invoker.call.call_args.kwargs
    stdin_body = invoker.call.call_args.args[0]
    assert not kwargs.get("system_prompt"), (
        "system_prompt must be absent/empty for judge"
    )
    assert stdin_body.startswith("JUDGE_SYS - Python\n\n"), (
        "stdin must carry combined prompt: system half + blank line + user half"
    )
    assert candidates[0].id in stdin_body


def test_judge_top_n_empty_candidates_returns_empty_list(run_log: RunLog) -> None:
    invoker = MagicMock(spec=ClaudeCliInvoker)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )
    results, usage = extractor.judge_top_n([])
    assert results == []
    assert usage.cost_usd == pytest.approx(0.0)
    invoker.call.assert_not_called()


def test_judge_top_n_transcript_has_no_system_prompt_field(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    candidates = _judge_candidates(2)
    verdicts_raw = _default_top_n_verdicts(candidates, count=2)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_top_n_response(verdicts_raw)),
    )
    extractor.judge_top_n(candidates)
    transcript_file = tmp_path / "llm_judge_match.transcripts.jsonl"
    entry = json.loads(transcript_file.read_text(encoding="utf-8").strip())
    assert "system_prompt" not in entry
    assert "prompt" in entry


def test_judge_top_n_response_with_unknown_id_raises_batch_malformed(
    run_log: RunLog,
) -> None:
    from application_pipeline.llm import ExtractorBatchMalformedError

    candidates = _judge_candidates(3)
    bad_verdicts = [
        {
            "id": "not-in-candidates",
            "rank": 1,
            "matched": [],
            "missing": [],
            "summary": "x",
        }
    ]
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_top_n_response(bad_verdicts)),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.judge_top_n(candidates)


def test_judge_top_n_response_with_rank_out_of_range_raises_batch_malformed(
    run_log: RunLog,
) -> None:
    from application_pipeline.llm import ExtractorBatchMalformedError

    candidates = _judge_candidates(3)
    bad_verdicts = [
        {"id": "cand-0", "rank": 6, "matched": [], "missing": [], "summary": "x"}
    ]
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_top_n_response(bad_verdicts)),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.judge_top_n(candidates)


def test_judge_top_n_response_with_duplicate_rank_raises_batch_malformed(
    run_log: RunLog,
) -> None:
    from application_pipeline.llm import ExtractorBatchMalformedError

    candidates = _judge_candidates(3)
    bad_verdicts = [
        {"id": "cand-0", "rank": 1, "matched": [], "missing": [], "summary": "x"},
        {"id": "cand-1", "rank": 1, "matched": [], "missing": [], "summary": "y"},
    ]
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_top_n_response(bad_verdicts)),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.judge_top_n(candidates)


def test_claude_extractor_judge_top_n_satisfies_llm_extractor_protocol(
    run_log: RunLog,
) -> None:
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=MagicMock(spec=ClaudeCliInvoker),
    )
    assert isinstance(extractor, LLMExtractor)
