from __future__ import annotations

from collections.abc import Mapping
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict, cast

from application_pipeline.cv_slot_contract import SLOT_NAME_SET

from .errors import ContentPoolError

_SECTION_RE = re.compile(r"^% ={3,} (.+?) ={3,}\s*$")
_ITEM_RE = re.compile(r"^%%% ITEM:\s*(\S+)\s*$")
_ALWAYS_RE = re.compile(r"^%%% always:\s*(true|false)\s*$")
_GROUP_RE = re.compile(r"^%%% group:\s*(\S.*?)\s*$")
_RELEVANCE_RE = re.compile(r"^%%% relevance:\s*(.+)$")
_RELEVANCE_ENTRY_RE = re.compile(r"^\s*(\w+)=(high|medium|low)\s*$")
_NEWCOMMAND_RE = re.compile(r"^\\newcommand\{\\([^}]+)\}")
_SECTION_TO_SLOT = {
    "Berufserfahrung": "resume_berufserfahrung",
    "Ausbildung": "resume_ausbildung",
    "Projekte": "resume_projekte",
}
_RESUME_SLOT_NAMES = frozenset(_SECTION_TO_SLOT.values())


RelevanceLevel = Literal["high", "medium", "low"]


class PoolItem(TypedDict):
    section: str
    always: bool
    group: str | None
    relevance: dict[str, RelevanceLevel]


class ContentPoolCandidate(TypedDict):
    name: str
    always: bool
    group: str | None
    relevance: dict[str, RelevanceLevel]


@dataclass(frozen=True)
class ContentPoolDocument:
    _items: dict[str, PoolItem]
    _candidates_by_slot: dict[str, tuple[ContentPoolCandidate, ...]] = field(
        init=False,
        default_factory=dict,
    )
    _grouped_candidates_by_slot: dict[
        str, dict[str, tuple[ContentPoolCandidate, ...]]
    ] = field(
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
                    relevance=_validate_relevance_mapping(item["relevance"], name),
                )
                for name, item in self._items.items()
                if _SECTION_TO_SLOT.get(item["section"]) == slot_name
            )
            for slot_name in _RESUME_SLOT_NAMES
        }
        grouped_candidates_by_slot = {
            slot_name: _group_candidates(candidates)
            for slot_name, candidates in candidates_by_slot.items()
        }
        object.__setattr__(self, "_candidates_by_slot", candidates_by_slot)
        object.__setattr__(
            self, "_grouped_candidates_by_slot", grouped_candidates_by_slot
        )

    def candidates(self, slot_name: str) -> list[ContentPoolCandidate]:
        _validate_slot_name(slot_name)
        return list(self._candidates_by_slot[slot_name])

    def grouped_candidates(
        self, slot_name: str
    ) -> dict[str, list[ContentPoolCandidate]]:
        _validate_slot_name(slot_name)
        return {
            group: list(candidates)
            for group, candidates in self._grouped_candidates_by_slot[slot_name].items()
        }


def load(path: Path) -> ContentPoolDocument:
    return ContentPoolDocument(parse(path))


def _validate_slot_name(slot_name: str) -> None:
    if slot_name not in SLOT_NAME_SET:
        raise ContentPoolError(f"unknown content pool slot: {slot_name}")
    if slot_name not in _RESUME_SLOT_NAMES:
        raise ContentPoolError(f"content pool slot is not a resume slot: {slot_name}")


def parse(path: Path) -> dict[str, PoolItem]:
    result: dict[str, PoolItem] = {}
    current_section: str | None = None
    current_item: str | None = None
    always = False
    group: str | None = None
    relevance: dict[str, RelevanceLevel] = {}

    def commit() -> None:
        nonlocal current_item
        if current_item is not None:
            if current_section is None:
                raise ContentPoolError(
                    f"content pool item declared before any section header: {current_item}"
                )
            if current_item in result:
                raise ContentPoolError(
                    f"duplicate content pool item declaration: {current_item}"
                )
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
        elif m := _NEWCOMMAND_RE.match(line):
            if m.group(1) != current_item:
                raise ContentPoolError(
                    "content pool item metadata does not match following "
                    f"newcommand: {current_item}"
                )
            commit()
        elif _is_metadata_gap_line(line):
            continue
        else:
            commit()

    commit()
    return result


def _parse_relevance(raw: str, macro_name: str) -> dict[str, RelevanceLevel]:
    out: dict[str, RelevanceLevel] = {}
    for entry in raw.split(","):
        m = _RELEVANCE_ENTRY_RE.match(entry)
        if not m:
            raise ContentPoolError(
                f"malformed relevance entry for {macro_name!r}: {entry.strip()!r}"
            )
        out[m.group(1)] = cast(RelevanceLevel, m.group(2))
    return out


def _is_metadata_gap_line(line: str) -> bool:
    stripped = line.strip()
    return stripped == "" or (
        stripped.startswith("%")
        and not _SECTION_RE.match(line)
        and not _ITEM_RE.match(line)
        and not _ALWAYS_RE.match(line)
        and not _GROUP_RE.match(line)
        and not _RELEVANCE_RE.match(line)
    )


def _validate_relevance_mapping(
    relevance: Mapping[str, str], macro_name: str
) -> dict[str, RelevanceLevel]:
    if not isinstance(relevance, Mapping):
        raise ContentPoolError(
            f"malformed relevance entry for {macro_name!r}: expected mapping"
        )

    out: dict[str, RelevanceLevel] = {}
    for topic, level in relevance.items():
        if not isinstance(topic, str):
            raise ContentPoolError(
                f"malformed relevance entry for {macro_name!r}: invalid topic {topic!r}"
            )
        if level not in {"high", "medium", "low"}:
            raise ContentPoolError(
                f"malformed relevance entry for {macro_name!r}: {topic}={level!r}"
            )
        out[topic] = cast(RelevanceLevel, level)
    return out


def _group_candidates(
    candidates: tuple[ContentPoolCandidate, ...],
) -> dict[str, tuple[ContentPoolCandidate, ...]]:
    grouped: dict[str, list[ContentPoolCandidate]] = {}
    for candidate in candidates:
        group = candidate["group"]
        if group is None:
            continue
        grouped.setdefault(group, []).append(candidate)
    return {group: tuple(items) for group, items in grouped.items()}
