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

    @property
    def raw_description(self) -> str: ...


class DomainPreFilter:
    def __init__(self, negative_keywords: list[str]) -> None:
        self._blacklist = [n for k in negative_keywords if (n := normalize(k))]

    def classify(self, position: _Position) -> PreFilterVerdict:
        title_hay = normalize(position.title) or ""
        blacklist_matches = tuple(
            TermMatch(term=k, fields=frozenset({"title"}))
            for k in self._blacklist
            if k in title_hay
        )
        return PreFilterVerdict(
            passes=not bool(blacklist_matches),
            blacklist_matches=blacklist_matches,
        )
