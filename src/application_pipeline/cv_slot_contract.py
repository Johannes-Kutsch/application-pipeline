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

COVER_PARAGRAPH_PATTERN_SLOTS: tuple[str, ...] = (
    "cover_intro",
    "cover_pivot",
    "cover_fit",
    "cover_closing",
)

TEMPLATE_MARKERS: dict[str, str] = {
    slot_name: f"<<{slot_name.upper()}>>" for slot_name in SLOT_NAMES
}
TEMPLATE_MARKER_SET: frozenset[str] = frozenset(TEMPLATE_MARKERS.values())


def template_marker(slot_name: str) -> str:
    return TEMPLATE_MARKERS[slot_name]
