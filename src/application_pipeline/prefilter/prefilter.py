from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from application_pipeline.text import normalize


@dataclass(frozen=True)
class TermMatch:
    term: str


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
    blacklist_matches = tuple(TermMatch(term=k) for k in blacklist if k in title_hay)
    return PreFilterVerdict(
        passes=not blacklist_matches,
        blacklist_matches=blacklist_matches,
    )
