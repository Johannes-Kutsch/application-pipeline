import dataclasses

import pytest

from application_pipeline.llm import (
    CallUsage,
    ClassifyItem,
    JudgeCandidate,
    LLMExtractor,
    ExtractorError,
    MatchVerdict,
    RelevanceVerdict,
    StructuredExtract,
)
from application_pipeline.llm.types import ExtractorSchemaError


_EMPTY_EXTRACT = StructuredExtract(
    seniority=None,
    work_model=None,
    contract_type=None,
    key_skills=[],
    key_responsibilities=[],
    must_have_requirements=[],
    notable_caveats="",
)


# --- ExtractorError ---


def test_extractor_error_is_exception():
    with pytest.raises(ExtractorError):
        raise ExtractorError("boom")


def test_extractor_error_preserves_message():
    exc = ExtractorError("network timeout")
    assert str(exc) == "network timeout"


# --- RelevanceVerdict ---


def test_relevance_verdict_in_domain_true():
    v = RelevanceVerdict(in_domain=True, extract=_EMPTY_EXTRACT)
    assert v.in_domain is True


def test_relevance_verdict_in_domain_false():
    v = RelevanceVerdict(in_domain=False)
    assert v.in_domain is False


def test_relevance_verdict_in_domain_true_without_extract_raises():
    with pytest.raises(ExtractorSchemaError):
        RelevanceVerdict(in_domain=True)


def test_relevance_verdict_in_domain_false_with_extract_raises():
    with pytest.raises(ExtractorSchemaError):
        RelevanceVerdict(in_domain=False, extract=_EMPTY_EXTRACT)


def test_relevance_verdict_is_frozen():
    v = RelevanceVerdict(in_domain=True, extract=_EMPTY_EXTRACT)
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.in_domain = False  # type: ignore[misc]


# --- ClassifyItem ---


def test_classify_item_has_title_and_raw_description_only():
    item = ClassifyItem(title="Software Engineer", raw_description="Python role")
    assert item.title == "Software Engineer"
    assert item.raw_description == "Python role"
    assert not hasattr(item, "id")


def test_classify_item_is_frozen():
    item = ClassifyItem(title="T", raw_description="D")
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.title = "changed"  # type: ignore[misc]


# --- MatchVerdict ---


def test_match_verdict_fields():
    v = MatchVerdict(
        matched=["Python", "ML"],
        missing=["Go"],
        summary="Good fit overall.",
    )
    assert v.matched == ["Python", "ML"]
    assert v.missing == ["Go"]
    assert v.summary == "Good fit overall."


def test_match_verdict_is_frozen():
    v = MatchVerdict(matched=[], missing=[], summary="ok")
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.summary = "changed"  # type: ignore[misc]


def test_match_verdict_matched_entry_over_80_chars_succeeds():
    long_entry = "a" * 81
    v = MatchVerdict(
        matched=[long_entry],
        missing=[],
        summary="ok",
    )
    assert v.matched == [long_entry]


def test_match_verdict_missing_entry_over_80_chars_succeeds():
    long_entry = "b" * 81
    v = MatchVerdict(
        matched=[],
        missing=[long_entry],
        summary="ok",
    )
    assert v.missing == [long_entry]


def test_match_verdict_default_rank_is_one():
    v = MatchVerdict(matched=[], missing=[], summary="ok")
    assert v.rank == 1


@pytest.mark.parametrize("rank", [1, 2, 3, 4, 5])
def test_match_verdict_accepts_rank_in_range(rank: int):
    v = MatchVerdict(matched=[], missing=[], summary="ok", rank=rank)
    assert v.rank == rank


@pytest.mark.parametrize("rank", [0, -1, 6, 100])
def test_match_verdict_rejects_rank_out_of_range(rank: int):
    with pytest.raises(ExtractorSchemaError):
        MatchVerdict(matched=[], missing=[], summary="ok", rank=rank)


def test_match_verdict_summary_over_600_chars_succeeds():
    long_summary = "s" * 601
    v = MatchVerdict(
        matched=[],
        missing=[],
        summary=long_summary,
    )
    assert v.summary == long_summary


# --- LLMExtractor Protocol ---


_ZERO_USAGE = CallUsage(
    input_tokens=0, output_tokens=0, cache_read_tokens=0, cost_usd=0.0, duration_s=0.0
)


class _StubExtractor:
    def classify_relevance(
        self, item: ClassifyItem
    ) -> tuple[RelevanceVerdict, CallUsage]:
        return RelevanceVerdict(in_domain=True, extract=_EMPTY_EXTRACT), _ZERO_USAGE

    def judge_top_n(
        self, candidates: list[JudgeCandidate]
    ) -> tuple[list[MatchVerdict], CallUsage]:
        return [], _ZERO_USAGE


def test_conforming_class_is_llm_extractor():
    assert isinstance(_StubExtractor(), LLMExtractor)


def test_class_missing_classify_relevance_is_not_llm_extractor():
    class _Bad:
        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            return [], _ZERO_USAGE

    assert not isinstance(_Bad(), LLMExtractor)


def test_stub_classify_relevance_returns_relevance_verdict():
    extractor: LLMExtractor = _StubExtractor()
    item = ClassifyItem(title="Data Scientist", raw_description="some description")
    result, _ = extractor.classify_relevance(item)
    assert isinstance(result, RelevanceVerdict)
    assert result.in_domain is True
