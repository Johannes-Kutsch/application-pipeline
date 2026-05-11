from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from application_pipeline.language import Language
from application_pipeline.text import normalize


@dataclass(frozen=True)
class PreFilterVerdict:
    passes: bool


class _Position(Protocol):
    title: str
    raw_description: str


class DomainPreFilter:
    def __init__(
        self,
        inclusion_keywords: list[str],
        negative_keywords: list[str],
        skills: list[str],
    ) -> None:
        self._whitelist = [
            n for k in (*inclusion_keywords, *skills) if (n := normalize(k)) is not None
        ]
        self._blacklist = [
            n for k in negative_keywords if (n := normalize(k)) is not None
        ]

    def classify(self, position: _Position, language: Language) -> PreFilterVerdict:
        text = position.title + " " + position.raw_description
        haystack = normalize(text) or ""
        whitelist_hit = any(k in haystack for k in self._whitelist)
        blacklist_hit = any(k in haystack for k in self._blacklist)
        passes = whitelist_hit or not blacklist_hit
        return PreFilterVerdict(passes=passes)
