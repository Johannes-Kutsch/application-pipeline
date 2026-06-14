"""Tests for the CV slot contract surface."""

from __future__ import annotations

from application_pipeline.cv_slot_contract import (
    COVER_PARAGRAPH_PATTERN_SLOTS,
    SLOT_NAME_SET,
    SLOT_NAMES,
    TEMPLATE_MARKER_SET,
    TEMPLATE_MARKERS,
    template_marker,
)


def test_slot_names_match_cv_slot_map_vocabulary() -> None:
    assert len(SLOT_NAMES) == 13
    assert SLOT_NAMES[:4] == (
        "recipient_company",
        "recipient_name",
        "recipient_street",
        "recipient_zip_city",
    )
    assert SLOT_NAMES[4] == "opening"
    assert SLOT_NAMES[-4:] == (
        "resume_berufserfahrung",
        "resume_ausbildung",
        "resume_projekte",
        "skills_block",
    )


def test_slot_name_set_matches_slot_name_vocabulary() -> None:
    assert SLOT_NAME_SET == frozenset(SLOT_NAMES)


def test_cover_paragraph_pattern_slots_match_cover_projection() -> None:
    assert COVER_PARAGRAPH_PATTERN_SLOTS == (
        "cover_intro",
        "cover_pivot",
        "cover_fit",
        "cover_closing",
    )


def test_cover_paragraph_pattern_slots_are_projected_from_slot_names() -> None:
    assert COVER_PARAGRAPH_PATTERN_SLOTS == tuple(
        slot_name for slot_name in SLOT_NAMES if slot_name.startswith("cover_")
    )


def test_template_markers_preserve_uppercase_slot_marker_spelling() -> None:
    assert TEMPLATE_MARKERS == {
        slot_name: f"<<{slot_name.upper()}>>" for slot_name in SLOT_NAMES
    }


def test_template_marker_set_matches_slot_vocabulary_projection() -> None:
    assert TEMPLATE_MARKER_SET == frozenset(TEMPLATE_MARKERS.values())


def test_template_marker_returns_marker_for_each_known_slot() -> None:
    assert {slot_name: template_marker(slot_name) for slot_name in SLOT_NAMES} == (
        TEMPLATE_MARKERS
    )
