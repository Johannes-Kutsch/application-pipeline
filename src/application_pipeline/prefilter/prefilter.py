from __future__ import annotations

from dataclasses import dataclass, field
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
    whitelist_matches: tuple[TermMatch, ...] = field(default_factory=tuple)
    blacklist_matches: tuple[TermMatch, ...] = field(default_factory=tuple)


class _Position(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def raw_description(self) -> str: ...


def _find_matches(
    keywords: list[str], title_hay: str, body_hay: str
) -> list[TermMatch]:
    matches = []
    for k in keywords:
        in_title = k in title_hay
        in_body = k in body_hay
        if in_title or in_body:
            fields: frozenset[str] = frozenset(
                f for f, hit in (("title", in_title), ("body", in_body)) if hit
            )
            matches.append(TermMatch(term=k, fields=fields))
    return matches


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
        passes = whitelist_hit or not blacklist_hit
        return PreFilterVerdict(
            passes=passes,
            whitelist_hit=whitelist_hit,
            blacklist_hit=blacklist_hit,
            whitelist_matches=tuple(whitelist_matches),
            blacklist_matches=tuple(blacklist_matches),
        )
