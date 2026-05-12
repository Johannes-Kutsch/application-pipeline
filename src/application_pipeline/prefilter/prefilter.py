from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from application_pipeline.text import normalize


@dataclass(frozen=True)
class PreFilterVerdict:
    passes: bool
    whitelist_hit: bool
    blacklist_hit: bool


class _Position(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def raw_description(self) -> str: ...


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
        text = position.title + " " + position.raw_description
        haystack = normalize(text) or ""
        whitelist_hit = any(k in haystack for k in self._whitelist)
        blacklist_hit = any(k in haystack for k in self._blacklist)
        passes = whitelist_hit or not blacklist_hit
        return PreFilterVerdict(
            passes=passes, whitelist_hit=whitelist_hit, blacklist_hit=blacklist_hit
        )
