"""Tests for the CV slot contract surface."""

from __future__ import annotations

from application_pipeline.cv_slot_contract import (
    COVER_PARAGRAPH_PATTERN_SLOTS,
    SLOT_NAME_SET,
    SLOT_NAMES,
    TEMPLATE_MARKERS,
    template_marker,
)


def test_slot_names_match_cv_slot_map_vocabulary() -> None:
    assert SLOT_NAMES == (
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


def test_slot_name_set_matches_slot_name_vocabulary() -> None:
    assert SLOT_NAME_SET == frozenset(SLOT_NAMES)


def test_cover_paragraph_pattern_slots_match_cover_projection() -> None:
    assert COVER_PARAGRAPH_PATTERN_SLOTS == (
        "cover_intro",
        "cover_pivot",
        "cover_fit",
        "cover_closing",
    )


def test_template_markers_preserve_uppercase_slot_marker_spelling() -> None:
    assert TEMPLATE_MARKERS == {
        "recipient_company": "<<RECIPIENT_COMPANY>>",
        "recipient_name": "<<RECIPIENT_NAME>>",
        "recipient_street": "<<RECIPIENT_STREET>>",
        "recipient_zip_city": "<<RECIPIENT_ZIP_CITY>>",
        "opening": "<<OPENING>>",
        "cover_intro": "<<COVER_INTRO>>",
        "cover_pivot": "<<COVER_PIVOT>>",
        "cover_fit": "<<COVER_FIT>>",
        "cover_closing": "<<COVER_CLOSING>>",
        "resume_berufserfahrung": "<<RESUME_BERUFSERFAHRUNG>>",
        "resume_ausbildung": "<<RESUME_AUSBILDUNG>>",
        "resume_projekte": "<<RESUME_PROJEKTE>>",
        "skills_block": "<<SKILLS_BLOCK>>",
    }


def test_template_marker_returns_marker_for_each_known_slot() -> None:
    assert {slot_name: template_marker(slot_name) for slot_name in SLOT_NAMES} == (
        TEMPLATE_MARKERS
    )
