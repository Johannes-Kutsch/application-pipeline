import pytest

from application_pipeline.llm import (
    LLMExtractor,
    LLMExtractorError,
    MatchTier,
    MatchVerdict,
    RelevanceVerdict,
)


# --- LLMExtractorError ---


def test_llm_extractor_error_is_exception():
    with pytest.raises(LLMExtractorError):
        raise LLMExtractorError("boom")


def test_llm_extractor_error_preserves_message():
    exc = LLMExtractorError("network timeout")
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
    with pytest.raises(Exception):
        v.in_domain = False  # type: ignore[misc]


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
    with pytest.raises(Exception):
        v.tier = MatchTier.red  # type: ignore[misc]


# --- LLMExtractor Protocol ---


class _StubExtractor:
    def classify_relevance(
        self, language: str, title: str, raw_description: str
    ) -> RelevanceVerdict:
        return RelevanceVerdict(in_domain=True)

    def judge_match(self, language: str, raw_description: str) -> MatchVerdict:
        return MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok")


def test_conforming_class_is_llm_extractor():
    assert isinstance(_StubExtractor(), LLMExtractor)


def test_class_missing_judge_match_is_not_llm_extractor():
    class _Bad:
        def classify_relevance(
            self, language: str, title: str, raw_description: str
        ) -> RelevanceVerdict:
            return RelevanceVerdict(in_domain=False)

    assert not isinstance(_Bad(), LLMExtractor)


def test_class_missing_classify_relevance_is_not_llm_extractor():
    class _Bad:
        def judge_match(self, language: str, raw_description: str) -> MatchVerdict:
            return MatchVerdict(tier=MatchTier.red, matched=[], missing=[], summary="x")

    assert not isinstance(_Bad(), LLMExtractor)


def test_stub_classify_relevance_returns_relevance_verdict():
    extractor: LLMExtractor = _StubExtractor()
    result = extractor.classify_relevance("de", "Data Scientist", "some description")
    assert isinstance(result, RelevanceVerdict)
    assert result.in_domain is True


def test_stub_judge_match_returns_match_verdict():
    extractor: LLMExtractor = _StubExtractor()
    result = extractor.judge_match("en", "some description")
    assert isinstance(result, MatchVerdict)
    assert result.tier is MatchTier.green
