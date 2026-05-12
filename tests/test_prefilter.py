import dataclasses
from dataclasses import dataclass

import pytest

from application_pipeline.prefilter import DomainPreFilter, PreFilterVerdict


@dataclass
class StubPosition:
    title: str
    raw_description: str


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
    )
    verdict = filter_with_skill.classify(pos)
    assert verdict.passes is False


def test_blacklist_and_skill_passes(filter_with_skill: DomainPreFilter) -> None:
    pos = StubPosition(
        title="Pflegekraft mit Python-Kenntnissen",
        raw_description="Python-Entwicklung für Pflegesoftware.",
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
    )
    verdict = f.classify(pos)
    assert verdict.passes is True


def test_strasse_and_straße_match_same_listing() -> None:
    f = DomainPreFilter(
        inclusion_keywords=[],
        negative_keywords=["straße"],
        skills=[],
    )
    pos = StubPosition(
        title="Job in der Hauptstrasse",
        raw_description="Wir befinden uns in der Hauptstrasse.",
    )
    verdict = f.classify(pos)
    assert verdict.passes is False


def test_blacklist_only_drop_exposes_verdict_fields(
    filter_with_skill: DomainPreFilter,
) -> None:
    pos = StubPosition(
        title="Pflegekraft gesucht",
        raw_description="Wir suchen eine Pflegekraft für unsere Einrichtung.",
    )
    verdict = filter_with_skill.classify(pos)
    assert verdict.passes is False
    assert verdict.blacklist_hit is True
    assert verdict.whitelist_hit is False


def test_whitelist_rescue_exposes_verdict_fields(
    filter_with_skill: DomainPreFilter,
) -> None:
    pos = StubPosition(
        title="Pflegekraft mit Python-Kenntnissen",
        raw_description="Python-Entwicklung für Pflegesoftware.",
    )
    verdict = filter_with_skill.classify(pos)
    assert verdict.passes is True
    assert verdict.whitelist_hit is True
    assert verdict.blacklist_hit is True


def test_no_hit_either_exposes_verdict_fields(
    filter_with_skill: DomainPreFilter,
) -> None:
    pos = StubPosition(
        title="Marketing Manager",
        raw_description="Wir suchen einen Marketing Manager.",
    )
    verdict = filter_with_skill.classify(pos)
    assert verdict.passes is True
    assert verdict.whitelist_hit is False
    assert verdict.blacklist_hit is False


def test_verdict_is_frozen() -> None:
    verdict = PreFilterVerdict(passes=True, whitelist_hit=False, blacklist_hit=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.passes = False  # type: ignore[misc]
