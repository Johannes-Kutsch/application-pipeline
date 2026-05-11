from __future__ import annotations

from typing import Literal, cast

from langdetect import LangDetectException, detect_langs

from application_pipeline.parsers.types import Position

Language = Literal["de", "en", "other", "unknown"]

_CONFIDENCE_FLOOR = 0.5


def resolve_language(position: Position) -> Language:
    if position.stub.language is not None:
        return cast(Language, position.stub.language)
    text = position.stub.title + " " + position.raw_description
    return _detect(text)


def _detect(text: str) -> Language:
    try:
        langs = detect_langs(text)
    except LangDetectException:
        return "unknown"
    if not langs or langs[0].prob < _CONFIDENCE_FLOOR:
        return "unknown"
    detected = langs[0].lang
    if detected in ("de", "en"):
        return cast(Language, detected)
    return "other"
