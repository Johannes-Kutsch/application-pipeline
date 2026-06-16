from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from application_pipeline.cv_slot_contract import (
    COVER_PARAGRAPH_PATTERN_SLOTS,
    SLOT_NAME_SET,
)

_PATTERN_HEADER_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_METADATA_RE = re.compile(r"^- ([a-z_]+):\s*(.*)$")
_PLACEHOLDER_RE = re.compile(r"\b(Muster[^\W\d_]+)\b")
_SENTENCE_RE = re.compile(r"[.!?](?:\s|$)")

_REQUIRED_METADATA = frozenset(
    {"slot", "argument_type", "use_when", "placeholders", "why_it_works"}
)
_VALID_SLOTS = frozenset(COVER_PARAGRAPH_PATTERN_SLOTS)
_VALID_PLACEHOLDERS = frozenset(
    {
        "Musterfirma",
        "Musterprodukt",
        "Musterprojekt",
        "Musterrolle",
        "Musterteam",
        "Musteraufgabe",
        "Musterbranche",
        "Musterdomäne",
        "Mustertechnologie",
        "Musterziel",
        "Musterort",
        "Musterkontakt",
    }
)


class CoverPatternError(Exception):
    pass


@dataclass(frozen=True)
class CoverPattern:
    name: str
    slot: str
    argument_type: str
    use_when: str
    placeholders: tuple[str, ...]
    why_it_works: str
    text: str


@dataclass(frozen=True)
class CoverPatternLibrary:
    _patterns: tuple[CoverPattern, ...] = ()

    def __post_init__(self) -> None:
        for pattern in self._patterns:
            _validate_pattern_placeholders(pattern)

    @classmethod
    def parse(cls, text: str) -> CoverPatternLibrary:
        stripped = text.strip()
        if not stripped:
            return cls()

        matches = list(_PATTERN_HEADER_RE.finditer(stripped))
        patterns: list[CoverPattern] = []

        for index, match in enumerate(matches):
            start = match.start()
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(stripped)
            )
            block = stripped[start:end].strip()
            patterns.append(_parse_block(block))

        return cls(tuple(patterns))

    @classmethod
    def load(cls, path: Path) -> CoverPatternLibrary:
        if not path.exists():
            return cls()
        text = path.read_text(encoding="utf-8-sig")
        if not text.strip():
            return cls()
        return cls.parse(text)

    def all_patterns(self) -> list[CoverPattern]:
        return list(self._patterns)

    def patterns_for_slot(self, slot: str) -> list[CoverPattern]:
        _validate_projection_slot(slot)
        return [pattern for pattern in self._patterns if pattern.slot == slot]


def parse_library(text: str) -> CoverPatternLibrary:
    return CoverPatternLibrary.parse(text)


def parse(text: str) -> list[CoverPattern]:
    return parse_library(text).all_patterns()


def load_library(path: Path) -> CoverPatternLibrary:
    return CoverPatternLibrary.load(path)


def load(path: Path) -> list[CoverPattern]:
    return load_library(path).all_patterns()


def _validate_projection_slot(slot: str) -> None:
    if slot in _VALID_SLOTS:
        return
    if slot in SLOT_NAME_SET:
        raise CoverPatternError(
            f"cover slot projection requires a cover paragraph slot, got: {slot}"
        )
    raise CoverPatternError(f"unknown cover slot: {slot}")


def _validate_pattern_placeholders(pattern: CoverPattern) -> None:
    _validate_declared_placeholders(pattern.name, pattern.placeholders)
    _validate_text_placeholders(pattern.name, pattern.placeholders, pattern.text)


def _validate_declared_placeholders(name: str, placeholders: tuple[str, ...]) -> None:
    unsupported = [item for item in placeholders if item not in _VALID_PLACEHOLDERS]
    if unsupported:
        unsupported_text = ", ".join(unsupported)
        raise CoverPatternError(f"{name}: unsupported placeholder: {unsupported_text}")


def _validate_text_placeholders(
    name: str, placeholders: tuple[str, ...], text: str
) -> None:
    text_placeholders = set(_PLACEHOLDER_RE.findall(text))
    undeclared = sorted(text_placeholders - set(placeholders))
    if undeclared:
        undeclared_text = ", ".join(undeclared)
        raise CoverPatternError(
            f"{name}: undeclared placeholders in text: {undeclared_text}"
        )


def _parse_block(block: str) -> CoverPattern:
    lines = block.splitlines()
    name = lines[0].removeprefix("## ").strip()
    metadata: dict[str, str] = {}
    body_start: int | None = None

    for index, line in enumerate(lines[1:], start=1):
        if body_start is not None:
            continue
        if match := _METADATA_RE.match(line):
            metadata[match.group(1)] = match.group(2).strip()
            continue
        if not line.strip():
            body_start = index + 1
            continue
        raise CoverPatternError(f"{name}: expected metadata bullets before text")

    missing = {
        key for key in _REQUIRED_METADATA if key not in metadata or not metadata[key]
    }
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise CoverPatternError(f"{name}: missing required metadata: {missing_text}")

    slot = metadata["slot"]
    if slot not in _VALID_SLOTS:
        raise CoverPatternError(f"{name}: unknown cover slot: {slot}")

    placeholders = tuple(
        item.strip() for item in metadata["placeholders"].split(",") if item.strip()
    )
    _validate_declared_placeholders(name, placeholders)

    body_lines = lines[body_start:] if body_start is not None else []
    paragraphs = [
        " ".join(line.strip() for line in chunk.splitlines() if line.strip())
        for chunk in "\n".join(body_lines).strip().split("\n\n")
        if chunk.strip()
    ]
    if not paragraphs:
        raise CoverPatternError(f"{name}: text paragraph is empty")
    if len(paragraphs) != 1:
        raise CoverPatternError(f"{name}: must contain exactly one paragraph")

    paragraph = paragraphs[0]
    if len(_SENTENCE_RE.findall(paragraph)) < 2:
        raise CoverPatternError(f"{name}: must contain at least two sentences")

    pattern = CoverPattern(
        name=name,
        slot=slot,
        argument_type=metadata["argument_type"],
        use_when=metadata["use_when"],
        placeholders=placeholders,
        why_it_works=metadata["why_it_works"],
        text=paragraph,
    )
    _validate_pattern_placeholders(pattern)
    return pattern
