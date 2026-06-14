from __future__ import annotations

SLOT_NAMES: tuple[str, ...] = (
    "recipient_company",
    "recipient_name",
    "recipient_street",
    "recipient_zip_city",
    "opening",
    "cover_intro",
    "cover_pivot",
    "cover_fit",
    "cover_closing",
    "resume_berufserfahrung",
    "resume_ausbildung",
    "resume_projekte",
    "skills_block",
)

SLOT_NAME_SET: frozenset[str] = frozenset(SLOT_NAMES)

_COVER_PARAGRAPH_SLOT_PREFIX = "cover_"

COVER_PARAGRAPH_PATTERN_SLOTS: tuple[str, ...] = tuple(
    slot_name
    for slot_name in SLOT_NAMES
    if slot_name.startswith(_COVER_PARAGRAPH_SLOT_PREFIX)
)

TEMPLATE_MARKERS: dict[str, str] = {
    slot_name: f"<<{slot_name.upper()}>>" for slot_name in SLOT_NAMES
}


def template_marker(slot_name: str) -> str:
    return TEMPLATE_MARKERS[slot_name]
