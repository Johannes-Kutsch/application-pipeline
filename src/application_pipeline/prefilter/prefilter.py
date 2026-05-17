from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from application_pipeline.text import normalize


@dataclass(frozen=True)
class TermMatch:
    term: str
    fields: frozenset[str]


@dataclass(frozen=True)
class PreFilterVerdict:
    passes: bool
    whitelist_hit: bool
    blacklist_hit: bool
    whitelist_matches: tuple[TermMatch, ...] = ()
    blacklist_matches: tuple[TermMatch, ...] = ()


class _Position(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def raw_description(self) -> str: ...


def _find_matches(
    keywords: list[str], title_hay: str, body_hay: str
) -> tuple[TermMatch, ...]:
    matches: list[TermMatch] = []
    for k in keywords:
        fields = frozenset(
            f for f, hay in (("title", title_hay), ("body", body_hay)) if k in hay
        )
        if fields:
            matches.append(TermMatch(term=k, fields=fields))
    return tuple(matches)


class DomainPreFilter:
    def __init__(
        self,
        inclusion_keywords: list[str],
        negative_keywords: list[str],
        skills: list[str],
    ) -> None:
        self._whitelist = [
            n for k in (*inclusion_keywords, *skills) if (n := normalize(k))
        ]
        self._blacklist = [n for k in negative_keywords if (n := normalize(k))]

    def classify(self, position: _Position) -> PreFilterVerdict:
        title_hay = normalize(position.title) or ""
        body_hay = normalize(position.raw_description) or ""
        whitelist_matches = _find_matches(self._whitelist, title_hay, body_hay)
        blacklist_matches = _find_matches(self._blacklist, title_hay, body_hay)
        whitelist_hit = bool(whitelist_matches)
        blacklist_hit = bool(blacklist_matches)
        return PreFilterVerdict(
            passes=whitelist_hit or not blacklist_hit,
            whitelist_hit=whitelist_hit,
            blacklist_hit=blacklist_hit,
            whitelist_matches=whitelist_matches,
            blacklist_matches=blacklist_matches,
        )
