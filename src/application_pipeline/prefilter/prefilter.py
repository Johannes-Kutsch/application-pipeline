from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, cast

from langdetect import LangDetectException, detect_langs

from application_pipeline.text import normalize

_CONFIDENCE_FLOOR = 0.5

Language = Literal["de", "en", "other", "unknown"]

_VALID_LANGUAGES: frozenset[str] = frozenset({"de", "en", "other", "unknown"})


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
        self._inclusion_lc = [
            n for k in inclusion_keywords if (n := normalize(k)) is not None
        ]
        self._negative_lc = [
            n for k in negative_keywords if (n := normalize(k)) is not None
        ]
        self._skills_lc = [n for k in skills if (n := normalize(k)) is not None]

    def classify(self, position: _Position) -> PreFilterVerdict:
        haystack = normalize(position.title + " " + position.raw_description) or ""
        whitelist = self._inclusion_lc + self._skills_lc
        whitelist_hit = any(k in haystack for k in whitelist)
        blacklist_hit = any(k in haystack for k in self._negative_lc)
        passes = whitelist_hit or not blacklist_hit

        if position.language is not None:
            language = cast(Language, position.language)
        else:
            language = _detect_language(position.title + " " + position.raw_description)

        return PreFilterVerdict(passes=passes, language=language)


def _detect_language(text: str) -> Language:
    try:
        langs = detect_langs(text)
        if not langs or langs[0].prob < _CONFIDENCE_FLOOR:
            return "unknown"
        detected = langs[0].lang
        if detected == "de":
            return "de"
        if detected == "en":
            return "en"
        return "other"
    except LangDetectException:
        return "unknown"
