import dataclasses

import pytest

from application_pipeline.llm import (
    CallUsage,
    ClassifyItem,
    JudgeCandidate,
    LLMExtractor,
    ExtractorError,
    MatchTier,
    MatchVerdict,
    RelevanceVerdict,
)
from application_pipeline.llm.types import ExtractorSchemaError


# --- ExtractorError ---


def test_extractor_error_is_exception():
    with pytest.raises(ExtractorError):
        raise ExtractorError("boom")


def test_extractor_error_preserves_message():
    exc = ExtractorError("network timeout")
    assert str(exc) == "network timeout"


# --- MatchTier ---


def test_match_tier_has_three_values():
    assert set(MatchTier) == {MatchTier.green, MatchTier.amber, MatchTier.red}


def test_match_tier_values_are_strings():
    assert MatchTier.green == "green"
    assert MatchTier.amber == "amber"
    assert MatchTier.red == "red"


# --- RelevanceVerdict ---


def test_relevance_verdict_in_domain_true():
    v = RelevanceVerdict(in_domain=True)
    assert v.in_domain is True


def test_relevance_verdict_in_domain_false():
    v = RelevanceVerdict(in_domain=False)
    assert v.in_domain is False


def test_relevance_verdict_is_frozen():
    v = RelevanceVerdict(in_domain=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.in_domain = False  # type: ignore[misc]


# --- ClassifyItem ---


def test_classify_item_fields():
    item = ClassifyItem(
        id="abc", title="Software Engineer", raw_description="Python role"
    )
    assert item.id == "abc"
    assert item.title == "Software Engineer"
    assert item.raw_description == "Python role"


def test_classify_item_is_frozen():
    item = ClassifyItem(id="x", title="T", raw_description="D")
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.id = "y"  # type: ignore[misc]


# --- MatchVerdict ---


def test_match_verdict_fields():
    v = MatchVerdict(
        tier=MatchTier.green,
        matched=["Python", "ML"],
        missing=["Go"],
        summary="Good fit overall.",
    )
    assert v.tier is MatchTier.green
    assert v.matched == ["Python", "ML"]
    assert v.missing == ["Go"]
    assert v.summary == "Good fit overall."


def test_match_verdict_amber_tier():
    v = MatchVerdict(
        tier=MatchTier.amber, matched=[], missing=["Rust"], summary="Partial fit."
    )
    assert v.tier is MatchTier.amber


def test_match_verdict_red_tier():
    v = MatchVerdict(
        tier=MatchTier.red, matched=[], missing=["C++", "Embedded"], summary="Poor fit."
    )
    assert v.tier is MatchTier.red


def test_match_verdict_is_frozen():
    v = MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok")
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.tier = MatchTier.red  # type: ignore[misc]


def test_match_verdict_matched_entry_over_80_chars_succeeds():
    long_entry = "a" * 81
    v = MatchVerdict(
        tier=MatchTier.green,
        matched=[long_entry],
        missing=[],
        summary="ok",
    )
    assert v.matched == [long_entry]


def test_match_verdict_missing_entry_over_80_chars_succeeds():
    long_entry = "b" * 81
    v = MatchVerdict(
        tier=MatchTier.red,
        matched=[],
        missing=[long_entry],
        summary="ok",
    )
    assert v.missing == [long_entry]


def test_match_verdict_default_rank_is_one():
    v = MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok")
    assert v.rank == 1


@pytest.mark.parametrize("rank", [1, 2, 3, 4, 5])
def test_match_verdict_accepts_rank_in_range(rank: int):
    v = MatchVerdict(
        tier=MatchTier.green, matched=[], missing=[], summary="ok", rank=rank
    )
    assert v.rank == rank


@pytest.mark.parametrize("rank", [0, -1, 6, 100])
def test_match_verdict_rejects_rank_out_of_range(rank: int):
    with pytest.raises(ExtractorSchemaError):
        MatchVerdict(
            tier=MatchTier.green, matched=[], missing=[], summary="ok", rank=rank
        )


def test_match_verdict_summary_over_600_chars_succeeds():
    long_summary = "s" * 601
    v = MatchVerdict(
        tier=MatchTier.amber,
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
    def classify_relevance_batch(
        self, items: list[ClassifyItem]
    ) -> list[RelevanceVerdict]:
        return [RelevanceVerdict(in_domain=True) for _ in items]

    def judge_match(self, raw_description: str) -> MatchVerdict:
        return MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok")

    def judge_top_n(
        self, candidates: list[JudgeCandidate]
    ) -> tuple[list[MatchVerdict], CallUsage]:
        return [], _ZERO_USAGE


def test_conforming_class_is_llm_extractor():
    assert isinstance(_StubExtractor(), LLMExtractor)


def test_class_missing_judge_match_is_not_llm_extractor():
    class _Bad:
        def classify_relevance_batch(
            self, items: list[ClassifyItem]
        ) -> list[RelevanceVerdict]:
            return []

    assert not isinstance(_Bad(), LLMExtractor)


def test_class_missing_classify_relevance_batch_is_not_llm_extractor():
    class _Bad:
        def judge_match(self, raw_description: str) -> MatchVerdict:
            return MatchVerdict(tier=MatchTier.red, matched=[], missing=[], summary="x")

    assert not isinstance(_Bad(), LLMExtractor)


def test_stub_classify_relevance_batch_returns_relevance_verdicts():
    extractor: LLMExtractor = _StubExtractor()
    items = [
        ClassifyItem(id="0", title="Data Scientist", raw_description="some description")
    ]
    results = extractor.classify_relevance_batch(items)
    assert len(results) == 1
    assert isinstance(results[0], RelevanceVerdict)
    assert results[0].in_domain is True


def test_stub_judge_match_returns_match_verdict():
    extractor: LLMExtractor = _StubExtractor()
    result = extractor.judge_match("some description")
    assert isinstance(result, MatchVerdict)
    assert result.tier is MatchTier.green
