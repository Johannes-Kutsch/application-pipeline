from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

_H2_START_RE = re.compile(r"^## (.+)$")
_BULLET_START_RE = re.compile(r"^- (.+)$")
_ATTR_BLOCK_RE = re.compile(r"^(.*?)\s*\{([^}]*)\}\s*$")
_ATTR_TOKEN_RE = re.compile(r"^\s*(\w[\w-]*)(?:=([\w-]+))?\s*$")
_VALID_RELEVANCE_LEVELS = frozenset({"high", "medium", "low"})


@dataclass(frozen=True)
class SkillItem:
    name: str
    always: bool


@dataclass
class SkillGroup:
    name: str
    always: bool
    relevance: dict[str, str]
    items: list[SkillItem] = field(default_factory=list)


@dataclass(frozen=True)
class TriageSkillsDocument:
    judge_text: str
    groups: list[SkillGroup]

    @property
    def skill_groups(self) -> list[SkillGroup]:
        return self.groups


class _GroupAttrs(NamedTuple):
    always: bool
    relevance: dict[str, str]


def parse(text: str) -> list[SkillGroup]:
    return parse_document(text).groups


def load(path: Path) -> list[SkillGroup]:
    return load_document(path).groups


def load_document(path: Path) -> TriageSkillsDocument:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return TriageSkillsDocument(judge_text="", groups=[])
    return parse_document(text)


def parse_document(text: str) -> TriageSkillsDocument:
    groups: list[SkillGroup] = []
    current_group: SkillGroup | None = None
    judge_items: list[str] = []

    for line in text.splitlines():
        if m := _H2_START_RE.match(line):
            name, raw_attrs = _split_attrs(m.group(1))
            attrs = _parse_group_attrs(raw_attrs)
            current_group = SkillGroup(
                name=name,
                always=attrs.always,
                relevance=attrs.relevance,
            )
            groups.append(current_group)
        elif m := _BULLET_START_RE.match(line):
            body = m.group(1).strip()
            name, raw_attrs = _split_attrs(body)
            judge_items.append(f"- {_judge_name(body)}")
            if current_group is None:
                continue
            always = _parse_item_always(raw_attrs)
            current_group.items.append(SkillItem(name=name, always=always))

    return TriageSkillsDocument(
        judge_text="\n".join(judge_items),
        groups=groups,
    )


def _judge_name(body: str) -> str:
    if m := _ATTR_BLOCK_RE.match(body):
        return m.group(1).strip()
    return body.strip()


def _split_attrs(body: str) -> tuple[str, str | None]:
    if m := _ATTR_BLOCK_RE.match(body):
        return m.group(1).strip(), m.group(2)
    if "{" in body:
        name = body[: body.index("{")].strip()
        return name, None
    return body.strip(), None


def _parse_group_attrs(raw: str | None) -> _GroupAttrs:
    always = False
    relevance: dict[str, str] = {}
    if raw is None:
        return _GroupAttrs(always=always, relevance=relevance)
    for token in raw.split(","):
        m = _ATTR_TOKEN_RE.match(token)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key == "always" and value is None:
            always = True
        elif value in _VALID_RELEVANCE_LEVELS:
            relevance[key] = value
    return _GroupAttrs(always=always, relevance=relevance)


def _parse_item_always(raw: str | None) -> bool:
    if raw is None:
        return False
    for token in raw.split(","):
        m = _ATTR_TOKEN_RE.match(token)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key == "always" and value is None:
            return True
    return False


__all__ = [
    "SkillGroup",
    "SkillItem",
    "TriageSkillsDocument",
    "load",
    "load_document",
    "parse",
    "parse_document",
]
