"""Tests for the CV slot-map parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from application_pipeline.latex.slot_map import (
    MissingSlotError,
    UnknownSlotError,
    parse,
)

_CANONICAL_SLOTS = (
    "recipient_line_1",
    "recipient_line_2",
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


def _write_slot_map(path: Path, bodies: dict[str, str]) -> Path:
    text = "".join(f"%% SLOT: {name}\n{body}" for name, body in bodies.items())
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def well_formed(tmp_path: Path) -> Path:
    bodies = {name: f"body of {name}\n" for name in _CANONICAL_SLOTS}
    return _write_slot_map(tmp_path / "cv.tex", bodies)


def test_parse_well_formed_returns_all_slots(well_formed: Path) -> None:
    result = parse(well_formed)
    assert set(result) == set(_CANONICAL_SLOTS)
    for name in _CANONICAL_SLOTS:
        assert result[name] == f"body of {name}\n"


def test_parse_missing_slot_raises_with_names(tmp_path: Path) -> None:
    bodies = {name: "x\n" for name in _CANONICAL_SLOTS if name != "opening"}
    path = _write_slot_map(tmp_path / "cv.tex", bodies)
    with pytest.raises(MissingSlotError) as exc:
        parse(path)
    assert "opening" in str(exc.value)


def test_parse_multiple_missing_slots_lists_all(tmp_path: Path) -> None:
    bodies = {
        name: "x\n"
        for name in _CANONICAL_SLOTS
        if name not in {"opening", "cover_intro"}
    }
    path = _write_slot_map(tmp_path / "cv.tex", bodies)
    with pytest.raises(MissingSlotError) as exc:
        parse(path)
    message = str(exc.value)
    assert "opening" in message
    assert "cover_intro" in message


def test_parse_empty_body_returns_empty_string(tmp_path: Path) -> None:
    bodies = {name: ("" if name == "opening" else "x\n") for name in _CANONICAL_SLOTS}
    path = _write_slot_map(tmp_path / "cv.tex", bodies)
    result = parse(path)
    assert result["opening"] == ""


def test_parse_preserves_multiline_tex_verbatim(tmp_path: Path) -> None:
    intro_body = (
        "Sehr geehrte Damen und Herren,\n"
        "\n"
        "ich bewerbe mich auf die Stelle bei \\href{https://example.com}{Example GmbH}.\n"
        "  Eingerückte Zeile mit Umlauten: ÄÖÜß.\n"
        "\\textit{kursiv} und \\textbf{fett}.\n"
    )
    bodies = {
        name: (intro_body if name == "cover_intro" else "x\n")
        for name in _CANONICAL_SLOTS
    }
    path = _write_slot_map(tmp_path / "cv.tex", bodies)
    result = parse(path)
    assert result["cover_intro"] == intro_body


def test_parse_unknown_slot_raises(tmp_path: Path) -> None:
    bodies = {name: "x\n" for name in _CANONICAL_SLOTS}
    bodies["bogus_slot"] = "y\n"
    path = _write_slot_map(tmp_path / "cv.tex", bodies)
    with pytest.raises(UnknownSlotError) as exc:
        parse(path)
    assert "bogus_slot" in str(exc.value)
