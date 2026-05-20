from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

from .errors import ContentPoolError

_SECTION_RE = re.compile(r"^% ={3,} (.+?) ={3,}\s*$")
_ITEM_RE = re.compile(r"^%%% ITEM:\s*(\S+)\s*$")
_ALWAYS_RE = re.compile(r"^%%% always:\s*(true|false)\s*$")
_GROUP_RE = re.compile(r"^%%% group:\s*(\S.*?)\s*$")
_RELEVANCE_RE = re.compile(r"^%%% relevance:\s*(.+)$")
_RELEVANCE_ENTRY_RE = re.compile(r"^\s*(\w+)=(high|medium|low)\s*$")


class PoolItem(TypedDict):
    section: str
    always: bool
    group: str | None
    relevance: dict[str, str]


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
