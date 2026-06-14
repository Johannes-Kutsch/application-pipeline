from __future__ import annotations

import re
from pathlib import Path

from application_pipeline.cv_slot_contract import SLOT_NAMES

_HEADER = re.compile(r"^%% SLOT: (\S+)\s*$")

_CANONICAL_SLOTS: frozenset[str] = frozenset(SLOT_NAMES)


class SlotMapError(Exception):
    pass


class MissingSlotError(SlotMapError):
    pass


class UnknownSlotError(SlotMapError):
    pass


def parse(path: Path) -> dict[str, str]:
    """Parse a CV Slot-Map file into a dict mapping slot name to raw TeX body."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    slots: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in lines:
        m = _HEADER.match(line)
        if m:
            if current_name is not None:
                slots[current_name] = "".join(current_lines)
            current_name = m.group(1)
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        slots[current_name] = "".join(current_lines)

    unknown = set(slots) - _CANONICAL_SLOTS
    if unknown:
        raise UnknownSlotError(f"unknown slots: {', '.join(sorted(unknown))}")

    missing = _CANONICAL_SLOTS - set(slots)
    if missing:
        raise MissingSlotError(f"missing slots: {', '.join(sorted(missing))}")

    return slots
