import dataclasses
from dataclasses import dataclass

import pytest

from application_pipeline.prefilter import DomainPreFilter, PreFilterVerdict


@dataclass
class StubPosition:
    title: str
    raw_description: str
    language: str | None = None


@pytest.fixture
def filter_with_skill() -> DomainPreFilter:
    return DomainPreFilter(
        inclusion_keywords=[],
        negative_keywords=["pfleg", "pflege"],
        skills=["python"],
    )


def test_blacklist_only_does_not_pass(filter_with_skill: DomainPreFilter) -> None:
    pos = StubPosition(
        title="Pflegekraft gesucht",
        raw_description="Wir suchen eine Pflegekraft für unsere Einrichtung.",
        language="de",
    )
    verdict = filter_with_skill.classify(pos)
    assert verdict.passes is False


def test_blacklist_and_skill_passes(filter_with_skill: DomainPreFilter) -> None:
    pos = StubPosition(
        title="Pflegekraft mit Python-Kenntnissen",
        raw_description="Python-Entwicklung für Pflegesoftware.",
        language="de",
    )
    verdict = filter_with_skill.classify(pos)
    assert verdict.passes is True


def test_neither_whitelist_nor_blacklist_passes() -> None:
    f = DomainPreFilter(
        inclusion_keywords=[],
        negative_keywords=["pfleg"],
        skills=["python"],
    )
    pos = StubPosition(
        title="Marketing Manager",
        raw_description="Wir suchen einen Marketing Manager.",
        language="de",
    )
    verdict = f.classify(pos)
    assert verdict.passes is True


def test_whitelist_only_passes() -> None:
    f = DomainPreFilter(
        inclusion_keywords=["data engineer"],
        negative_keywords=[],
        skills=[],
    )
    pos = StubPosition(
        title="Senior Data Engineer",
        raw_description="We are looking for a data engineer.",
        language="en",
    )
    verdict = f.classify(pos)
    assert verdict.passes is True


def test_language_passthrough_when_set() -> None:
    f = DomainPreFilter(inclusion_keywords=[], negative_keywords=[], skills=[])
    for lang in ("de", "en", "other", "unknown"):
        pos = StubPosition(title="Job", raw_description="desc", language=lang)
        verdict = f.classify(pos)
        assert verdict.language == lang


def test_german_listing_detected_as_de() -> None:
    f = DomainPreFilter(inclusion_keywords=[], negative_keywords=[], skills=[])
    pos = StubPosition(
        title="Softwareentwickler gesucht",
        raw_description=(
            "Wir suchen einen erfahrenen Softwareentwickler für unser Team in Hamburg. "
            "Das Unternehmen bietet interessante Projekte und eine gute Bezahlung. "
            "Bewerben Sie sich jetzt mit Ihren vollständigen Unterlagen."
        ),
    )
    assert f.classify(pos).language == "de"


def test_english_listing_detected_as_en() -> None:
    f = DomainPreFilter(inclusion_keywords=[], negative_keywords=[], skills=[])
    pos = StubPosition(
        title="Software Engineer wanted",
        raw_description=(
            "We are looking for an experienced software engineer to join our team in London. "
            "The company offers interesting projects and competitive pay. "
            "Apply now with your full application documents."
        ),
    )
    assert f.classify(pos).language == "en"


def test_undetectable_text_gives_unknown() -> None:
    f = DomainPreFilter(inclusion_keywords=[], negative_keywords=[], skills=[])
    # Numbers-only text has no alphabetic features; langdetect raises LangDetectException
    pos = StubPosition(title="123", raw_description="456 789 012")
    assert f.classify(pos).language == "unknown"


def test_non_de_en_language_gives_other() -> None:
    f = DomainPreFilter(inclusion_keywords=[], negative_keywords=[], skills=[])
    pos = StubPosition(
        title="Ingénieur logiciel",
        raw_description=(
            "Nous recherchons un ingénieur logiciel expérimenté pour rejoindre notre équipe. "
            "L'entreprise offre des projets intéressants et une bonne rémunération. "
            "Postulez maintenant avec vos documents complets."
        ),
    )
    assert f.classify(pos).language == "other"


def test_strasse_and_straße_match_same_listing() -> None:
    f = DomainPreFilter(
        inclusion_keywords=[],
        negative_keywords=["straße"],
        skills=[],
    )
    pos = StubPosition(
        title="Job in der Hauptstrasse",
        raw_description="Wir befinden uns in der Hauptstrasse.",
        language="de",
    )
    verdict = f.classify(pos)
    assert verdict.passes is False


def test_verdict_is_frozen() -> None:
    verdict = PreFilterVerdict(passes=True, language="de")
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.passes = False  # type: ignore[misc]
