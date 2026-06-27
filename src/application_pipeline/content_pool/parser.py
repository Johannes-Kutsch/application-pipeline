from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from application_pipeline.cv_slot_contract import SLOT_NAME_SET

from .errors import ContentPoolError

_SECTION_RE = re.compile(r"^% ={3,} (.+?) ={3,}\s*$")
_ITEM_RE = re.compile(r"^%%% ITEM:\s*(\S+)\s*$")
_ALWAYS_RE = re.compile(r"^%%% always:\s*(true|false)\s*$")
_GROUP_RE = re.compile(r"^%%% group:\s*(\S.*?)\s*$")
_RELEVANCE_RE = re.compile(r"^%%% relevance:\s*(.+)$")
_RELEVANCE_ENTRY_RE = re.compile(r"^\s*(\w+)=(high|medium|low)\s*$")
_SECTION_TO_SLOT = {
    "Berufserfahrung": "resume_berufserfahrung",
    "Ausbildung": "resume_ausbildung",
    "Projekte": "resume_projekte",
}
_RESUME_SLOT_NAMES = frozenset(_SECTION_TO_SLOT.values())


class PoolItem(TypedDict):
    section: str
    always: bool
    group: str | None
    relevance: dict[str, str]


class ContentPoolCandidate(TypedDict):
    name: str
    always: bool
    group: str | None
    relevance: dict[str, str]


@dataclass(frozen=True)
class ContentPoolDocument:
    _items: dict[str, PoolItem]
    _candidates_by_slot: dict[str, tuple[ContentPoolCandidate, ...]] = field(
        init=False,
        default_factory=dict,
    )

    def __post_init__(self) -> None:
        candidates_by_slot = {
            slot_name: tuple(
                ContentPoolCandidate(
                    name=name,
                    always=item["always"],
                    group=item["group"],
                    relevance=item["relevance"],
                )
                for name, item in self._items.items()
                if _SECTION_TO_SLOT.get(item["section"]) == slot_name
            )
            for slot_name in _RESUME_SLOT_NAMES
        }
        object.__setattr__(self, "_candidates_by_slot", candidates_by_slot)

    def candidates(self, slot_name: str) -> list[ContentPoolCandidate]:
        _validate_slot_name(slot_name)
        return list(self._candidates_by_slot[slot_name])


def load(path: Path) -> ContentPoolDocument:
    return ContentPoolDocument(parse(path))


def _validate_slot_name(slot_name: str) -> None:
    if slot_name not in SLOT_NAME_SET:
        raise ContentPoolError(f"unknown content pool slot: {slot_name}")
    if slot_name not in _RESUME_SLOT_NAMES:
        raise ContentPoolError(f"content pool slot is not a resume slot: {slot_name}")


def parse(path: Path) -> dict[str, PoolItem]:
    result: dict[str, PoolItem] = {}
    current_section = ""
    current_item: str | None = None
    always = False
    group: str | None = None
    relevance: dict[str, str] = {}

    def commit() -> None:
        nonlocal current_item
        if current_item is not None:
            result[current_item] = PoolItem(
                section=current_section,
                always=always,
                group=group,
                relevance=relevance,
            )
            current_item = None

    for line in path.read_text(encoding="utf-8").splitlines():
        if m := _SECTION_RE.match(line):
            commit()
            current_section = m.group(1)
        elif m := _ITEM_RE.match(line):
            commit()
            current_item = m.group(1)
            always, group, relevance = False, None, {}
        elif current_item is None:
            continue
        elif m := _ALWAYS_RE.match(line):
            always = m.group(1) == "true"
        elif m := _GROUP_RE.match(line):
            group = m.group(1)
        elif m := _RELEVANCE_RE.match(line):
            relevance = _parse_relevance(m.group(1), current_item)
        else:
            commit()

    commit()
    return result


def _parse_relevance(raw: str, macro_name: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in raw.split(","):
        m = _RELEVANCE_ENTRY_RE.match(entry)
        if not m:
            raise ContentPoolError(
                f"malformed relevance entry for {macro_name!r}: {entry.strip()!r}"
            )
        out[m.group(1)] = m.group(2)
    return out
