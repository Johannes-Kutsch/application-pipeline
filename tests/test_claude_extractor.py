"""Tests for ClaudeExtractor — batched classify_relevance_batch + judge_top_n via Claude CLI."""

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
    ExtractorBatchMalformedError,
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
    JUDGE_MATCH_SLOTS,
    JUDGE_TOP_N_SLOTS,
    PromptTemplate,
    Prompts,
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
    classify: str = "classify: {ITEMS}",
    judge_top_n: str = "top-n: {skills} {candidates}",
) -> Prompts:
    return Prompts(
        classify_relevance=PromptTemplate(classify, CLASSIFY_RELEVANCE_SLOTS),
        judge_match=PromptTemplate(
            "judge: {skills} {raw_description}", JUDGE_MATCH_SLOTS
        ),
        judge_top_n=PromptTemplate(judge_top_n, JUDGE_TOP_N_SLOTS),
    )


def _usage() -> ClaudeUsage:
    return ClaudeUsage(input_tokens=100, output_tokens=20, cache_read_tokens=0)


def _classify_raw(verdicts: object) -> str:
    return f"<verdicts>{json.dumps(verdicts)}</verdicts>"


def _classify_response(raw_result: object) -> ClaudeResponse:
    return ClaudeResponse(
        raw_response=_classify_raw(raw_result),
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


def _batch_response(
    items: list[ClassifyItem], in_domain_map: dict[str, bool] | None = None
) -> ClaudeResponse:
    """Build a valid batch classify response for the given items."""
    if in_domain_map is None:
        in_domain_map = {item.id: True for item in items}
    result: list[dict[str, object]] = []
    for item in items:
        in_domain = in_domain_map.get(item.id, True)
        entry: dict[str, object] = {"id": item.id, "in_domain": in_domain}
        if in_domain:
            entry["extract"] = _DEFAULT_EXTRACT
        result.append(entry)
    return ClaudeResponse(
        raw_response=_classify_raw(result),
        usage=_usage(),
        cost_usd=0.001,
        duration_s=0.5,
        session_id="sess-1",
    )


def _fake_invoker(response: ClaudeResponse) -> MagicMock:
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.return_value = response
    return invoker


def _items(n: int = 3) -> list[ClassifyItem]:
    return [
        ClassifyItem(id=str(i), title=f"Title {i}", raw_description=f"Desc {i}")
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# classify_relevance_batch: happy path
# ---------------------------------------------------------------------------


def test_classify_relevance_batch_returns_in_domain_true(run_log: RunLog) -> None:
    items = _items(1)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_batch_response(items, {"0": True})),
    )
    results, usage = extractor.classify_relevance_batch(items)
    assert len(results) == 1
    assert isinstance(results[0], RelevanceVerdict)
    assert results[0].in_domain is True
    assert usage.input_tokens == 100
    assert usage.cost_usd == pytest.approx(0.001)


def test_classify_relevance_batch_returns_in_domain_false(run_log: RunLog) -> None:
    items = _items(1)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_batch_response(items, {"0": False})),
    )
    results, _ = extractor.classify_relevance_batch(items)
    assert results[0].in_domain is False


def test_classify_relevance_batch_n3_items_framed_in_prompt(run_log: RunLog) -> None:
    """With N=3, the rendered prompt contains exactly three item blocks."""
    items = _items(3)
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(classify="EN: {ITEMS}"),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )
    extractor.classify_relevance_batch(items)
    prompt_sent = invoker.call.call_args.args[0]
    # Each item is framed with [Item id=N]
    for item in items:
        assert f"[Item id={item.id}]" in prompt_sent
        assert item.title in prompt_sent
        assert item.raw_description in prompt_sent


def test_classify_relevance_batch_returns_verdicts_in_input_order(
    run_log: RunLog,
) -> None:
    """Id-keyed parse returns verdicts in input order regardless of response order."""
    items = [
        ClassifyItem(id="a", title="A", raw_description="da"),
        ClassifyItem(id="b", title="B", raw_description="db"),
        ClassifyItem(id="c", title="C", raw_description="dc"),
    ]
    # Response returns in reverse order
    reversed_result = [
        {"id": "c", "in_domain": False},
        {"id": "b", "in_domain": True, "extract": _DEFAULT_EXTRACT},
        {"id": "a", "in_domain": True, "extract": _DEFAULT_EXTRACT},
    ]
    response = ClaudeResponse(
        raw_response=_classify_raw(reversed_result),
        usage=_usage(),
        cost_usd=0.001,
        duration_s=0.5,
        session_id="s",
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(response),
    )
    results, _ = extractor.classify_relevance_batch(items)
    assert len(results) == 3
    assert results[0].in_domain is True  # id=a
    assert results[1].in_domain is True  # id=b
    assert results[2].in_domain is False  # id=c


# ---------------------------------------------------------------------------
# classify_relevance_batch: structured extract on in-domain verdicts
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
    item = ClassifyItem(
        id="0", title="Data Engineer", raw_description="Python+SQL role"
    )
    raw_result = [{"id": "0", "in_domain": True, "extract": _EXTRACT_PAYLOAD}]
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_classify_response(raw_result)),
    )
    results, _ = extractor.classify_relevance_batch([item])
    assert results[0].in_domain is True
    assert results[0].extract is not None
    assert results[0].extract.seniority == "senior"
    assert results[0].extract.work_model == "remote"
    assert results[0].extract.contract_type == "permanent"
    assert results[0].extract.key_skills == ["Python", "SQL"]
    assert results[0].extract.key_responsibilities == ["Build pipelines"]
    assert results[0].extract.must_have_requirements == ["5+ years Python"]
    assert results[0].extract.notable_caveats == ""


def test_classify_out_of_domain_verdict_has_extract_none(run_log: RunLog) -> None:
    item = ClassifyItem(id="0", title="Cashier", raw_description="Shop job")
    raw_result = [{"id": "0", "in_domain": False}]
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_classify_response(raw_result)),
    )
    results, _ = extractor.classify_relevance_batch([item])
    assert results[0].in_domain is False
    assert results[0].extract is None


def test_classify_in_domain_missing_extract_raises_batch_malformed(
    run_log: RunLog,
) -> None:
    item = ClassifyItem(id="0", title="Engineer", raw_description="Tech role")
    raw_result = [{"id": "0", "in_domain": True}]
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_classify_response(raw_result)),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch([item])


def test_classify_in_domain_malformed_extract_raises_batch_malformed(
    run_log: RunLog,
) -> None:
    item = ClassifyItem(id="0", title="Engineer", raw_description="Tech role")
    raw_result = [{"id": "0", "in_domain": True, "extract": "not-a-dict"}]
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_classify_response(raw_result)),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch([item])


def test_classify_in_domain_extract_missing_required_field_raises_batch_malformed(
    run_log: RunLog,
) -> None:
    item = ClassifyItem(id="0", title="Engineer", raw_description="Tech role")
    incomplete_extract = {"seniority": "senior", "work_model": "remote"}
    raw_result = [{"id": "0", "in_domain": True, "extract": incomplete_extract}]
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_classify_response(raw_result)),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch([item])


def test_classify_mixed_batch_in_domain_gets_extract_out_of_domain_gets_none(
    run_log: RunLog,
) -> None:
    items = [
        ClassifyItem(id="a", title="Engineer", raw_description="Tech role"),
        ClassifyItem(id="b", title="Cashier", raw_description="Shop job"),
        ClassifyItem(id="c", title="Data Scientist", raw_description="ML role"),
    ]
    raw_result = [
        {"id": "a", "in_domain": True, "extract": _EXTRACT_PAYLOAD},
        {"id": "b", "in_domain": False},
        {"id": "c", "in_domain": True, "extract": _EXTRACT_PAYLOAD},
    ]
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_classify_response(raw_result)),
    )
    results, _ = extractor.classify_relevance_batch(items)
    assert results[0].in_domain is True
    assert results[0].extract is not None
    assert results[1].in_domain is False
    assert results[1].extract is None
    assert results[2].in_domain is True
    assert results[2].extract is not None


# ---------------------------------------------------------------------------
# classify_relevance_batch: transcript recording
# ---------------------------------------------------------------------------


def test_classify_relevance_batch_records_events_row_to_call_site_file(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    items = _items(2)
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )

    extractor.classify_relevance_batch(items)

    events_file = tmp_path / "llm_classify_relevance.events.jsonl"
    assert events_file.exists()
    entry = json.loads(events_file.read_text(encoding="utf-8").strip())
    assert entry["event"] == "classify_relevance_batch"
    assert "cost_usd" in entry
    assert "duration_s" in entry
    assert entry["batch_size"] == 2


def test_classify_relevance_batch_records_transcript(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)
    items = _items(2)
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )

    extractor.classify_relevance_batch(items)

    transcript_file = tmp_path / "llm_classify_relevance.transcripts.jsonl"
    assert transcript_file.exists()
    entry = json.loads(transcript_file.read_text(encoding="utf-8").strip())
    assert entry["call"] == "classify_relevance_batch"
    assert entry["batch_size"] == 2
    assert "prompt" in entry
    assert "raw_response" in entry
    assert "usage" in entry
    assert "cost_usd" in entry
    assert "duration_s" in entry


# ---------------------------------------------------------------------------
# classify_relevance_batch: error mapping (call-site-specific shape validators)
# ---------------------------------------------------------------------------


def test_classify_batch_usage_limit_propagates(run_log: RunLog) -> None:
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
        extractor.classify_relevance_batch(_items(1))


def test_classify_batch_length_mismatch_raises_batch_malformed(run_log: RunLog) -> None:
    items = _items(3)
    # Response has only 2 entries
    short_result = [{"id": "0", "in_domain": True}, {"id": "1", "in_domain": False}]
    response = ClaudeResponse(
        raw_response=_classify_raw(short_result),
        usage=_usage(),
        cost_usd=0.0,
        duration_s=0.1,
        session_id="s",
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(response),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch(items)


def test_classify_batch_missing_id_raises_batch_malformed(run_log: RunLog) -> None:
    items = _items(2)
    # Response has unknown id
    bad_result = [{"id": "0", "in_domain": True}, {"id": "99", "in_domain": False}]
    response = ClaudeResponse(
        raw_response=_classify_raw(bad_result),
        usage=_usage(),
        cost_usd=0.0,
        duration_s=0.1,
        session_id="s",
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(response),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch(items)


def test_classify_batch_extra_id_raises_batch_malformed(run_log: RunLog) -> None:
    """Extra id detected as duplicate when length matches but one id is unknown."""
    items = [ClassifyItem(id="a", title="T", raw_description="D")]
    # Response has 1 entry but with a different id
    bad_result = [{"id": "z", "in_domain": True}]
    response = ClaudeResponse(
        raw_response=_classify_raw(bad_result),
        usage=_usage(),
        cost_usd=0.0,
        duration_s=0.1,
        session_id="s",
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(response),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch(items)


def test_classify_batch_non_list_response_raises_batch_malformed(
    run_log: RunLog,
) -> None:
    non_list = {"in_domain": True}
    response = ClaudeResponse(
        raw_response=_classify_raw(non_list),
        usage=_usage(),
        cost_usd=0.0,
        duration_s=0.1,
        session_id="s",
    )
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(response),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch(_items(1))


# ---------------------------------------------------------------------------
# claude_extractor.* files are never created
# ---------------------------------------------------------------------------


def test_successful_classify_does_not_write_claude_extractor_files(
    tmp_path: Path,
) -> None:
    run_log = RunLog(tmp_path)
    items = _items(1)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=_fake_invoker(_batch_response(items)),
    )
    extractor.classify_relevance_batch(items)

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
        extractor.classify_relevance_batch(_items(1))

    assert not (tmp_path / "claude_extractor.transcripts.jsonl").exists()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Model / effort pinning
# ---------------------------------------------------------------------------


def test_classify_relevance_batch_passes_haiku_model_to_invoker(
    run_log: RunLog,
) -> None:
    items = _items(1)
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )
    extractor.classify_relevance_batch(items)
    call_kwargs = invoker.call.call_args.kwargs
    assert call_kwargs["model"] == "haiku"
    assert call_kwargs.get("effort", "") == ""


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
# Slot drift tests
# ---------------------------------------------------------------------------


def test_classify_slots_match_inventory(run_log: RunLog) -> None:
    items = [ClassifyItem(id="0", title="MY_TITLE", raw_description="MY_DESC")]
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(classify="content={ITEMS}"),
        search_terms=_SEARCH_TERMS,
        run_log=run_log,
        _invoker=invoker,
    )
    extractor.classify_relevance_batch(items)
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
            lambda e: e.classify_relevance_batch(_items(2)),
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
            lambda e: e.classify_relevance_batch(_items(1)),
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
            lambda e: e.classify_relevance_batch(_items(1)),
            "llm_classify_relevance.transcripts.jsonl",
            ExtractorBatchMalformedError,
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
            lambda e: e.classify_relevance_batch(_items(1)),
            "llm_classify_relevance.transcripts.jsonl",
            ExtractorBatchMalformedError,
            "<verdicts>bad json</verdicts>",
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


def test_judge_top_n_response_with_unknown_id_raises_batch_malformed(
    run_log: RunLog,
) -> None:
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
