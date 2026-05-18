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
    blacklist_matches: tuple[TermMatch, ...] = ()


class _Position(Protocol):
    @property
    def title(self) -> str: ...


def precompute_blacklist(negative_keywords: list[str]) -> list[str]:
    return [n for k in negative_keywords if (n := normalize(k))]


def classify_position(position: _Position, blacklist: list[str]) -> PreFilterVerdict:
    title_hay = normalize(position.title) or ""
    blacklist_matches = tuple(
        TermMatch(term=k, fields=frozenset({"title"}))
        for k in blacklist
        if k in title_hay
    )
    return PreFilterVerdict(
        passes=not blacklist_matches,
        blacklist_matches=blacklist_matches,
    )


class DomainPreFilter:
    def __init__(self, negative_keywords: list[str]) -> None:
        self._blacklist = precompute_blacklist(negative_keywords)

    def classify(self, position: _Position) -> PreFilterVerdict:
        return classify_position(position, self._blacklist)
