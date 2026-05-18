import dataclasses
from dataclasses import dataclass

import pytest

from application_pipeline.prefilter import DomainPreFilter, PreFilterVerdict, TermMatch


@dataclass
class StubPosition:
    title: str
    raw_description: str


@pytest.fixture
def blacklist_filter() -> DomainPreFilter:
    return DomainPreFilter(negative_keywords=["pfleg", "pflege"])


def test_title_negative_keyword_drops(blacklist_filter: DomainPreFilter) -> None:
    pos = StubPosition(
        title="Pflegekraft gesucht",
        raw_description="Wir suchen eine Pflegekraft für unsere Einrichtung.",
    )
    verdict = blacklist_filter.classify(pos)
    assert verdict.passes is False


def test_body_only_negative_keyword_passes() -> None:
    f = DomainPreFilter(negative_keywords=["pfleg"])
    pos = StubPosition(
        title="Software Engineer",
        raw_description="Wir suchen eine Pflegekraft.",
    )
    verdict = f.classify(pos)
    assert verdict.passes is True


def test_no_negative_keyword_passes() -> None:
    f = DomainPreFilter(negative_keywords=["pfleg"])
    pos = StubPosition(
        title="Marketing Manager",
        raw_description="Wir suchen einen Marketing Manager.",
    )
    verdict = f.classify(pos)
    assert verdict.passes is True


def test_strasse_and_straße_match_same_listing() -> None:
    f = DomainPreFilter(negative_keywords=["straße"])
    pos = StubPosition(
        title="Job in der Hauptstrasse",
        raw_description="Wir befinden uns in der Hauptstrasse.",
    )
    verdict = f.classify(pos)
    assert verdict.passes is False


def test_title_drop_exposes_verdict_fields(blacklist_filter: DomainPreFilter) -> None:
    pos = StubPosition(
        title="Pflegekraft gesucht",
        raw_description="Wir suchen eine Pflegekraft für unsere Einrichtung.",
    )
    verdict = blacklist_filter.classify(pos)
    assert verdict.passes is False
    assert len(verdict.blacklist_matches) > 0


def test_passed_verdict_has_empty_blacklist_matches() -> None:
    f = DomainPreFilter(negative_keywords=["pfleg"])
    pos = StubPosition(
        title="Software Engineer",
        raw_description="Normale Stelle.",
    )
    verdict = f.classify(pos)
    assert verdict.passes is True
    assert verdict.blacklist_matches == ()


def test_verdict_is_frozen() -> None:
    verdict = PreFilterVerdict(passes=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.passes = False  # type: ignore[misc]


def test_blacklist_match_carries_term_and_title_field() -> None:
    f = DomainPreFilter(negative_keywords=["pfleg"])
    pos = StubPosition(
        title="Pflegekraft Stelle",
        raw_description="",
    )
    verdict = f.classify(pos)
    assert len(verdict.blacklist_matches) == 1
    match = verdict.blacklist_matches[0]
    assert isinstance(match, TermMatch)
    assert match.term == "pfleg"
    assert match.fields == frozenset({"title"})


def test_verdict_has_no_whitelist_attributes() -> None:
    verdict = PreFilterVerdict(passes=True)
    assert not hasattr(verdict, "whitelist_hit")
    assert not hasattr(verdict, "whitelist_matches")
