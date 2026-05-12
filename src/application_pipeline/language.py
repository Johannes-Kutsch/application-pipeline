from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from langdetect import LangDetectException, detect_langs

from application_pipeline.parsers.types import Position

Language = Literal["de", "en"]

_CONFIDENCE_FLOOR = 0.5


@dataclass(frozen=True)
class LanguageResolution:
    effective: Literal["de", "en"]
    detected: Literal["de", "en", "other", "unknown"]
    source: Literal["parser", "langdetect"]


def resolve_language(position: Position) -> LanguageResolution:
    if position.stub.language is not None:
        lang = position.stub.language
        return LanguageResolution(effective=lang, detected=lang, source="parser")
    text = position.stub.title + " " + position.raw_description
    detected = _detect(text)
    effective: Literal["de", "en"] = detected if detected in ("de", "en") else "en"
    return LanguageResolution(
        effective=cast(Literal["de", "en"], effective),
        detected=detected,
        source="langdetect",
    )


def _detect(text: str) -> Literal["de", "en", "other", "unknown"]:
    try:
        langs = detect_langs(text)
    except LangDetectException:
        return "unknown"
    if not langs or langs[0].prob < _CONFIDENCE_FLOOR:
        return "unknown"
    detected = langs[0].lang
    if detected in ("de", "en"):
        return cast(Literal["de", "en"], detected)
    return "other"
