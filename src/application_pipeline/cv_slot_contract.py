from __future__ import annotations

SLOT_NAMES: tuple[str, ...] = (
    "recipient_company",
    "recipient_name",
    "recipient_street",
    "recipient_zip_city",
    "cover_subject",
    "opening",
    "cover_intro",
    "cover_bullets",
    "cover_closing",
    "resume_berufserfahrung",
    "resume_ausbildung",
    "resume_projekte",
    "skills_block",
)

SLOT_NAME_SET: frozenset[str] = frozenset(SLOT_NAMES)

COVER_PARAGRAPH_PATTERN_SLOTS: tuple[str, ...] = (
    "cover_intro",
    "cover_closing",
)

TEMPLATE_MARKERS: dict[str, str] = {
    slot_name: f"<<{slot_name.upper()}>>" for slot_name in SLOT_NAMES
}
TEMPLATE_MARKER_SET: frozenset[str] = frozenset(TEMPLATE_MARKERS.values())


def template_marker(slot_name: str) -> str:
    return TEMPLATE_MARKERS[slot_name]
