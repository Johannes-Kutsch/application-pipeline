"""Tests for ClaudeExtractor — batched classify_relevance_batch + judge_match via Claude CLI."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import application_pipeline.parser_log as parser_log
from application_pipeline import (
    ClassifyItem,
    Config,
    LLMExtractor,
    MatchTier,
    MatchVerdict,
    RelevanceVerdict,
    SourceEntry,
)
from application_pipeline.llm import (
    ClaudeExtractor,
    ClaudeCliInvoker,
    ClaudeResponse,
    ClaudeUsage,
    ExtractorBatchMalformedError,
    ExtractorMalformedJSONError,
    ExtractorSchemaError,
    ExtractorUnreachableError,
)
from application_pipeline.llm.claude_cli import (
    ClaudeCliError,
    ClaudeMalformedEnvelopeError,
    ClaudeUsageLimitError,
)
from application_pipeline.prompts import (
    CLASSIFY_RELEVANCE_SLOTS,
    JUDGE_MATCH_SLOTS,
    PromptTemplate,
    Prompts,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_logs() -> Generator[None, None, None]:
    parser_log._logs_dir = None
    yield
    parser_log._logs_dir = None


def _config(**kwargs: object) -> Config:
    defaults: dict[str, object] = dict(
        keywords=["python"],
        skills=[],
        sources=[SourceEntry(parser_type="bundesagentur")],
        locations=["Hamburg"],
        claude_cli_path="claude",
    )
    defaults.update(kwargs)
    return Config(**defaults)  # type: ignore[arg-type]


def _prompts(
    classify_de: str = "DE: {ITEMS}",
    classify_en: str = "EN: {ITEMS}",
    judge_de: str = "DE judge: {skills} {raw_description}",
    judge_en: str = "EN judge: {skills} {raw_description}",
) -> Prompts:
    return Prompts(
        classify_relevance={
            "de": PromptTemplate(classify_de, CLASSIFY_RELEVANCE_SLOTS),
            "en": PromptTemplate(classify_en, CLASSIFY_RELEVANCE_SLOTS),
        },
        judge_match={
            "de": PromptTemplate(judge_de, JUDGE_MATCH_SLOTS),
            "en": PromptTemplate(judge_en, JUDGE_MATCH_SLOTS),
        },
    )


def _usage() -> ClaudeUsage:
    return ClaudeUsage(input_tokens=100, output_tokens=20, cache_read_tokens=0)


def _classify_raw(verdicts: object) -> str:
    return f"<verdicts>{json.dumps(verdicts)}</verdicts>"


def _judge_raw(verdict: object) -> str:
    return f"<verdict>{json.dumps(verdict)}</verdict>"


def _batch_response(
    items: list[ClassifyItem], in_domain_map: dict[str, bool] | None = None
) -> ClaudeResponse:
    """Build a valid batch classify response for the given items."""
    if in_domain_map is None:
        in_domain_map = {item.id: True for item in items}
    result = [
        {"id": item.id, "in_domain": in_domain_map.get(item.id, True)} for item in items
    ]
    return ClaudeResponse(
        raw_response=_classify_raw(result),
        usage=_usage(),
        cost_usd=0.001,
        duration_s=0.5,
        session_id="sess-1",
    )


_JUDGE_VERDICT = {
    "tier": "green",
    "matched": ["python"],
    "missing": [],
    "summary": "Good match",
}


def _judge_response() -> ClaudeResponse:
    return ClaudeResponse(
        raw_response=_judge_raw(_JUDGE_VERDICT),
        usage=_usage(),
        cost_usd=0.002,
        duration_s=1.2,
        session_id="sess-2",
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


def test_classify_relevance_batch_returns_in_domain_true() -> None:
    items = _items(1)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        _invoker=_fake_invoker(_batch_response(items, {"0": True})),
    )
    results, usage = extractor.classify_relevance_batch("en", items)
    assert len(results) == 1
    assert isinstance(results[0], RelevanceVerdict)
    assert results[0].in_domain is True
    assert usage.input_tokens == 100
    assert usage.cost_usd == pytest.approx(0.001)


def test_classify_relevance_batch_returns_in_domain_false() -> None:
    items = _items(1)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        _invoker=_fake_invoker(_batch_response(items, {"0": False})),
    )
    results, _ = extractor.classify_relevance_batch("en", items)
    assert results[0].in_domain is False


def test_classify_relevance_batch_n3_items_framed_in_prompt() -> None:
    """With N=3, the rendered prompt contains exactly three item blocks."""
    items = _items(3)
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(
        _config(), _prompts(classify_en="EN: {ITEMS}"), _invoker=invoker
    )
    extractor.classify_relevance_batch("en", items)
    prompt_sent = invoker.call.call_args.args[0]
    # Each item is framed with [Item id=N]
    for item in items:
        assert f"[Item id={item.id}]" in prompt_sent
        assert item.title in prompt_sent
        assert item.raw_description in prompt_sent


def test_classify_relevance_batch_returns_verdicts_in_input_order() -> None:
    """Id-keyed parse returns verdicts in input order regardless of response order."""
    items = [
        ClassifyItem(id="a", title="A", raw_description="da"),
        ClassifyItem(id="b", title="B", raw_description="db"),
        ClassifyItem(id="c", title="C", raw_description="dc"),
    ]
    # Response returns in reverse order
    reversed_result = [
        {"id": "c", "in_domain": False},
        {"id": "b", "in_domain": True},
        {"id": "a", "in_domain": True},
    ]
    response = ClaudeResponse(
        raw_response=_classify_raw(reversed_result),
        usage=_usage(),
        cost_usd=0.001,
        duration_s=0.5,
        session_id="s",
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=_fake_invoker(response))
    results, _ = extractor.classify_relevance_batch("en", items)
    assert len(results) == 3
    assert results[0].in_domain is True  # id=a
    assert results[1].in_domain is True  # id=b
    assert results[2].in_domain is False  # id=c


# ---------------------------------------------------------------------------
# classify_relevance_batch: language routing
# ---------------------------------------------------------------------------


def test_classify_relevance_batch_uses_german_prompt_for_de() -> None:
    items = _items(1)
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(classify_de="DE: {ITEMS}", classify_en="EN: {ITEMS}"),
        _invoker=invoker,
    )
    extractor.classify_relevance_batch("de", items)
    prompt_sent = invoker.call.call_args.args[0]
    assert prompt_sent.startswith("DE:")


def test_classify_relevance_batch_uses_english_prompt_for_en() -> None:
    items = _items(1)
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(classify_de="DE: {ITEMS}", classify_en="EN: {ITEMS}"),
        _invoker=invoker,
    )
    extractor.classify_relevance_batch("en", items)
    prompt_sent = invoker.call.call_args.args[0]
    assert prompt_sent.startswith("EN:")


def test_classify_relevance_batch_falls_back_to_english_for_unknown() -> None:
    items = _items(1)
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(classify_en="EN: {ITEMS}"),
        _invoker=invoker,
    )
    extractor.classify_relevance_batch("fr", items)
    prompt_sent = invoker.call.call_args.args[0]
    assert prompt_sent.startswith("EN:")


# ---------------------------------------------------------------------------
# classify_relevance_batch: transcript recording
# ---------------------------------------------------------------------------


def test_classify_relevance_batch_records_transcript(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    items = _items(2)
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)

    extractor.classify_relevance_batch("en", items)

    transcript_file = tmp_path / "claude_extractor.transcripts.jsonl"
    assert transcript_file.exists()
    entry = json.loads(transcript_file.read_text(encoding="utf-8").strip())
    assert entry["call"] == "classify_relevance_batch"
    assert entry["language"] == "en"
    assert entry["batch_size"] == 2
    assert "prompt" in entry
    assert "raw_response" in entry
    assert "usage" in entry
    assert "cost_usd" in entry
    assert "duration_s" in entry


# ---------------------------------------------------------------------------
# classify_relevance_batch: error mapping (call-site-specific shape validators)
# ---------------------------------------------------------------------------


def test_classify_batch_usage_limit_propagates() -> None:
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeUsageLimitError(
        "rate limit", returncode=1, stdout="", stderr="rate limit", envelope=None
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)
    with pytest.raises(ClaudeUsageLimitError):
        extractor.classify_relevance_batch("en", _items(1))


def test_classify_batch_length_mismatch_raises_batch_malformed() -> None:
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
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=_fake_invoker(response))
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch("en", items)


def test_classify_batch_missing_id_raises_batch_malformed() -> None:
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
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=_fake_invoker(response))
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch("en", items)


def test_classify_batch_extra_id_raises_batch_malformed() -> None:
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
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=_fake_invoker(response))
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch("en", items)


def test_classify_batch_non_list_response_raises_batch_malformed() -> None:
    non_list = {"in_domain": True}
    response = ClaudeResponse(
        raw_response=_classify_raw(non_list),
        usage=_usage(),
        cost_usd=0.0,
        duration_s=0.1,
        session_id="s",
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=_fake_invoker(response))
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.classify_relevance_batch("en", _items(1))


# ---------------------------------------------------------------------------
# judge_match: happy path
# ---------------------------------------------------------------------------


def test_judge_match_returns_match_verdict() -> None:
    extractor = ClaudeExtractor(
        _config(), _prompts(), _invoker=_fake_invoker(_judge_response())
    )
    result, usage = extractor.judge_match("en", "Looking for Python dev")
    assert isinstance(result, MatchVerdict)
    assert result.tier == MatchTier.green
    assert result.matched == ["python"]
    assert result.missing == []
    assert result.summary == "Good match"
    assert usage.input_tokens == 100
    assert usage.cost_usd == pytest.approx(0.002)


# ---------------------------------------------------------------------------
# judge_match: language routing
# ---------------------------------------------------------------------------


def test_judge_match_uses_german_prompt_for_de() -> None:
    invoker = _fake_invoker(_judge_response())
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)
    extractor.judge_match("de", "Stelle")
    prompt_sent = invoker.call.call_args.args[0]
    assert prompt_sent.startswith("DE judge:")


def test_judge_match_uses_english_prompt_for_en() -> None:
    invoker = _fake_invoker(_judge_response())
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)
    extractor.judge_match("en", "Job posting")
    prompt_sent = invoker.call.call_args.args[0]
    assert prompt_sent.startswith("EN judge:")


def test_judge_match_falls_back_to_english_for_unknown() -> None:
    invoker = _fake_invoker(_judge_response())
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)
    extractor.judge_match("fr", "Job posting")
    prompt_sent = invoker.call.call_args.args[0]
    assert prompt_sent.startswith("EN judge:")


# ---------------------------------------------------------------------------
# judge_match: skills rendering
# ---------------------------------------------------------------------------


def test_judge_match_renders_skills_into_prompt() -> None:
    invoker = _fake_invoker(_judge_response())
    extractor = ClaudeExtractor(
        _config(skills=["python", "docker"]),
        _prompts(judge_en="skills={skills} desc={raw_description}"),
        _invoker=invoker,
    )
    extractor.judge_match("en", "desc")
    prompt_sent = invoker.call.call_args.args[0]
    assert "- python" in prompt_sent
    assert "- docker" in prompt_sent


def test_judge_match_skills_bound_at_construction() -> None:
    invoker = _fake_invoker(_judge_response())
    extractor = ClaudeExtractor(
        _config(skills=["go"]),
        _prompts(judge_en="s={skills} d={raw_description}"),
        _invoker=invoker,
    )
    extractor.judge_match("en", "MY_DESC")
    prompt_sent = invoker.call.call_args.args[0]
    assert "- go" in prompt_sent
    assert "d=MY_DESC" in prompt_sent


# ---------------------------------------------------------------------------
# judge_match: transcript recording
# ---------------------------------------------------------------------------


def test_judge_match_records_transcript(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    invoker = _fake_invoker(_judge_response())
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)

    extractor.judge_match("en", "Looking for Python dev")

    transcript_file = tmp_path / "claude_extractor.transcripts.jsonl"
    assert transcript_file.exists()
    entry = json.loads(transcript_file.read_text(encoding="utf-8").strip())
    assert entry["call"] == "judge_match"
    assert entry["language"] == "en"
    assert "prompt" in entry
    assert entry["raw_response"] == _judge_raw(_JUDGE_VERDICT)
    assert entry["usage"]["input_tokens"] == 100
    assert entry["cost_usd"] == pytest.approx(0.002)
    assert entry["duration_s"] == pytest.approx(1.2)


def test_both_calls_append_to_same_transcript_file(tmp_path: Path) -> None:
    parser_log.configure(tmp_path)
    items = _items(1)
    extractor = ClaudeExtractor(
        _config(),
        _prompts(),
        _invoker=MagicMock(
            spec=ClaudeCliInvoker,
            **{"call.side_effect": [_batch_response(items), _judge_response()]},
        ),
    )
    extractor.classify_relevance_batch("en", items)
    extractor.judge_match("en", "desc")

    lines = (
        (tmp_path / "claude_extractor.transcripts.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
    assert len(lines) == 2
    assert json.loads(lines[0])["call"] == "classify_relevance_batch"
    assert json.loads(lines[1])["call"] == "judge_match"


# ---------------------------------------------------------------------------
# judge_match: error mapping (call-site-specific shape validators)
# ---------------------------------------------------------------------------


def test_judge_usage_limit_propagates() -> None:
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeUsageLimitError(
        "rate limit", returncode=1, stdout="", stderr="rate limit", envelope=None
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)
    with pytest.raises(ClaudeUsageLimitError):
        extractor.judge_match("en", "desc")


def test_judge_missing_tier_raises_schema_error() -> None:
    bad_verdict = {"matched": [], "missing": [], "summary": "x"}
    bad = ClaudeResponse(
        raw_response=_judge_raw(bad_verdict),
        usage=_usage(),
        cost_usd=0.0,
        duration_s=0.1,
        session_id="s",
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=_fake_invoker(bad))
    with pytest.raises(ExtractorSchemaError):
        extractor.judge_match("en", "desc")


def test_judge_invalid_tier_value_raises_schema_error() -> None:
    bad_verdict = {"tier": "invalid", "matched": [], "missing": [], "summary": "x"}
    bad = ClaudeResponse(
        raw_response=_judge_raw(bad_verdict),
        usage=_usage(),
        cost_usd=0.0,
        duration_s=0.1,
        session_id="s",
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=_fake_invoker(bad))
    with pytest.raises(ExtractorSchemaError):
        extractor.judge_match("en", "desc")


def test_judge_summary_over_600_chars_raises_schema_error() -> None:
    bad_verdict = {"tier": "green", "matched": [], "missing": [], "summary": "x" * 601}
    bad = ClaudeResponse(
        raw_response=_judge_raw(bad_verdict),
        usage=_usage(),
        cost_usd=0.0,
        duration_s=0.1,
        session_id="s",
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=_fake_invoker(bad))
    with pytest.raises(ExtractorSchemaError):
        extractor.judge_match("en", "desc")


# ---------------------------------------------------------------------------
# Failure-path: does not pollute the success transcript file
# ---------------------------------------------------------------------------


def test_classify_failure_transcript_does_not_write_to_extractor_file(
    tmp_path: Path,
) -> None:
    parser_log.configure(tmp_path)
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeCliError(
        "exit 1",
        returncode=1,
        stdout="",
        stderr="",
        envelope=None,
        envelope_error_class="cli_nonzero_exit",
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)

    with pytest.raises(ExtractorUnreachableError):
        extractor.classify_relevance_batch("en", _items(1))

    assert not (tmp_path / "claude_extractor.transcripts.jsonl").exists()


# ---------------------------------------------------------------------------
# prewarm
# ---------------------------------------------------------------------------


def test_prewarm_is_noop() -> None:
    invoker = MagicMock(spec=ClaudeCliInvoker)
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)
    extractor.prewarm()  # must not raise
    invoker.call.assert_not_called()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Model / effort pinning
# ---------------------------------------------------------------------------


def test_classify_relevance_batch_passes_haiku_model_to_invoker() -> None:
    items = _items(1)
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)
    extractor.classify_relevance_batch("en", items)
    call_kwargs = invoker.call.call_args.kwargs
    assert call_kwargs["model"] == "haiku"
    assert call_kwargs.get("effort", "") == ""


def test_judge_match_passes_sonnet_model_and_medium_effort_to_invoker() -> None:
    invoker = _fake_invoker(_judge_response())
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)
    extractor.judge_match("en", "desc")
    call_kwargs = invoker.call.call_args.kwargs
    assert call_kwargs["model"] == "sonnet"
    assert call_kwargs["effort"] == "medium"


def test_claude_extractor_is_llm_extractor() -> None:
    extractor = ClaudeExtractor(
        _config(), _prompts(), _invoker=MagicMock(spec=ClaudeCliInvoker)
    )
    assert isinstance(extractor, LLMExtractor)


# ---------------------------------------------------------------------------
# Slot drift tests
# ---------------------------------------------------------------------------


def test_classify_slots_match_inventory() -> None:
    items = [ClassifyItem(id="0", title="MY_TITLE", raw_description="MY_DESC")]
    invoker = _fake_invoker(_batch_response(items))
    extractor = ClaudeExtractor(
        _config(),
        _prompts(classify_en="content={ITEMS}"),
        _invoker=invoker,
    )
    extractor.classify_relevance_batch("en", items)
    prompt_sent = invoker.call.call_args.args[0]
    assert "MY_TITLE" in prompt_sent
    assert "MY_DESC" in prompt_sent


def test_judge_slots_match_inventory() -> None:
    invoker = _fake_invoker(_judge_response())
    extractor = ClaudeExtractor(
        _config(skills=["python"]),
        _prompts(judge_en="s={skills} d={raw_description}"),
        _invoker=invoker,
    )
    extractor.judge_match("en", "MY_DESC")
    prompt_sent = invoker.call.call_args.args[0]
    assert "- python" in prompt_sent
    assert "d=MY_DESC" in prompt_sent


# ---------------------------------------------------------------------------
# Parametrised failure-path tests: CLI error, malformed envelope,
# tag missing, JSON malformed — parameter selects the call site.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invoke,transcript_file,stderr,extra_transcript_assertions",
    [
        pytest.param(
            lambda e: e.classify_relevance_batch("en", _items(2)),
            "classify_relevance.transcripts.jsonl",
            "some stderr",
            {"stdout": '{"type":"result","result":"","is_error":false}'},
            id="classify",
        ),
        pytest.param(
            lambda e: e.judge_match("en", "desc", stub_url="https://example.com/job/1"),
            "judge_match.transcripts.jsonl",
            "judge stderr",
            {"stub_url": "https://example.com/job/1"},
            id="judge",
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
    parser_log.configure(tmp_path)
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeCliError(
        "empty result",
        returncode=0,
        stdout='{"type":"result","result":"","is_error":false}',
        stderr=stderr,
        envelope={"type": "result", "result": "", "is_error": False},
        envelope_error_class="empty_result",
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)

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
            lambda e: e.classify_relevance_batch("en", _items(1)),
            "classify_relevance.transcripts.jsonl",
            {},
            id="classify",
        ),
        pytest.param(
            lambda e: e.judge_match("en", "desc", stub_url="https://example.com/job/2"),
            "judge_match.transcripts.jsonl",
            {"stub_url": "https://example.com/job/2"},
            id="judge",
        ),
    ],
)
def test_malformed_envelope(
    tmp_path: Path,
    invoke: Callable[[ClaudeExtractor], object],
    transcript_file: str,
    extra_transcript_assertions: dict[str, object],
) -> None:
    parser_log.configure(tmp_path)
    invoker = MagicMock(spec=ClaudeCliInvoker)
    invoker.call.side_effect = ClaudeMalformedEnvelopeError(
        "bad json",
        returncode=0,
        stdout="not-json",
        stderr="",
        envelope=None,
        envelope_error_class="envelope_not_json",
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=invoker)

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
            lambda e: e.classify_relevance_batch("en", _items(1)),
            "classify_relevance.transcripts.jsonl",
            ExtractorBatchMalformedError,
            id="classify",
        ),
        pytest.param(
            lambda e: e.judge_match("en", "desc"),
            "judge_match.transcripts.jsonl",
            ExtractorMalformedJSONError,
            id="judge",
        ),
    ],
)
def test_tag_missing(
    tmp_path: Path,
    invoke: Callable[[ClaudeExtractor], object],
    transcript_file: str,
    expected_error_cls: type,
) -> None:
    parser_log.configure(tmp_path)
    raw = "no tags here"
    response = ClaudeResponse(
        raw_response=raw, usage=_usage(), cost_usd=0.0, duration_s=0.1, session_id="s"
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=_fake_invoker(response))

    with pytest.raises(expected_error_cls):
        invoke(extractor)

    entry = json.loads((tmp_path / transcript_file).read_text(encoding="utf-8"))
    assert entry["envelope_error_class"] == "tag_missing"
    assert entry["raw_response"] == raw


@pytest.mark.parametrize(
    "invoke,transcript_file,expected_error_cls,raw",
    [
        pytest.param(
            lambda e: e.classify_relevance_batch("en", _items(1)),
            "classify_relevance.transcripts.jsonl",
            ExtractorBatchMalformedError,
            "<verdicts>bad json</verdicts>",
            id="classify",
        ),
        pytest.param(
            lambda e: e.judge_match("en", "desc"),
            "judge_match.transcripts.jsonl",
            ExtractorMalformedJSONError,
            "<verdict>bad json</verdict>",
            id="judge",
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
    parser_log.configure(tmp_path)
    response = ClaudeResponse(
        raw_response=raw, usage=_usage(), cost_usd=0.0, duration_s=0.1, session_id="s"
    )
    extractor = ClaudeExtractor(_config(), _prompts(), _invoker=_fake_invoker(response))

    with pytest.raises(expected_error_cls):
        invoke(extractor)

    entry = json.loads((tmp_path / transcript_file).read_text(encoding="utf-8"))
    assert entry["envelope_error_class"] == "json_malformed"
    assert entry["raw_response"] == raw
