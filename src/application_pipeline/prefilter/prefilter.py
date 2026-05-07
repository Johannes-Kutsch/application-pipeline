from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, cast

from langdetect import LangDetectException, detect_langs

from application_pipeline.text import normalize

_CONFIDENCE_FLOOR = 0.5

Language = Literal["de", "en", "other", "unknown"]


@dataclass(frozen=True)
class PreFilterVerdict:
    passes: bool
    language: Language


class _Position(Protocol):
    title: str
    raw_description: str
    language: str | None


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

    def classify(self, position: _Position) -> PreFilterVerdict:
        text = position.title + " " + position.raw_description
        haystack = normalize(text) or ""
        whitelist_hit = any(k in haystack for k in self._whitelist)
        blacklist_hit = any(k in haystack for k in self._blacklist)
        passes = whitelist_hit or not blacklist_hit

        if position.language is not None:
            language = cast(Language, position.language)
        else:
            language = _detect_language(text)

        return PreFilterVerdict(passes=passes, language=language)


def _detect_language(text: str) -> Language:
    try:
        langs = detect_langs(text)
    except LangDetectException:
        return "unknown"
    if not langs or langs[0].prob < _CONFIDENCE_FLOOR:
        return "unknown"
    detected = langs[0].lang
    if detected == "de" or detected == "en":
        return detected
    return "other"
