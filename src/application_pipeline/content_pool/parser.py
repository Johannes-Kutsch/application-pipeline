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
    lines = path.read_text(encoding="utf-8").splitlines()
    result: dict[str, PoolItem] = {}
    current_section = ""

    i = 0
    while i < len(lines):
        line = lines[i]

        section_m = _SECTION_RE.match(line)
        if section_m:
            current_section = section_m.group(1)
            i += 1
            continue

        item_m = _ITEM_RE.match(line)
        if item_m:
            macro_name = item_m.group(1)
            always: bool = False
            group: str | None = None
            relevance: dict[str, str] = {}

            i += 1
            while i < len(lines):
                meta_line = lines[i]
                always_m = _ALWAYS_RE.match(meta_line)
                if always_m:
                    always = always_m.group(1) == "true"
                    i += 1
                    continue
                group_m = _GROUP_RE.match(meta_line)
                if group_m:
                    group = group_m.group(1)
                    i += 1
                    continue
                rel_m = _RELEVANCE_RE.match(meta_line)
                if rel_m:
                    relevance = _parse_relevance(rel_m.group(1), macro_name)
                    i += 1
                    continue
                break

            result[macro_name] = PoolItem(
                section=current_section,
                always=always,
                group=group,
                relevance=relevance,
            )
            continue

        i += 1

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
