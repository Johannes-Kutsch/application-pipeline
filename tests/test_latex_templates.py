"""Tests for the LaTeX template files — identity-token leak prevention."""

from __future__ import annotations

import importlib.resources
import re
from pathlib import Path

import pytest

from application_pipeline.latex import slot_map

_EXPECTED_LATEX_PACKAGE_FILES = frozenset(
    {
        "cv_template.tex",
        "slot_map.py",
        # Vendored moderncv 1.2.0 distribution (ADR-0034).
        "moderncv.cls",
        "moderncvcolorblack.sty",
        "moderncvcolorblue.sty",
        "moderncvcolorgreen.sty",
        "moderncvcolorgrey.sty",
        "moderncvcolororange.sty",
        "moderncvcolorpurple.sty",
        "moderncvcolorred.sty",
        "moderncvcompatibility.sty",
        "moderncvstylebanking.sty",
        "moderncvstylecasual.sty",
        "moderncvstyleclassic.sty",
        "moderncvstyleempty.sty",
        "moderncvstyleoldstyle.sty",
        "tweaklist.sty",
    }
)

_IDENTITY_TOKENS = (
    "<<ADDRESS_STREET>>",
    "<<ADDRESS_CITY>>",
    "<<PHONE>>",
    "<<EMAIL>>",
    "<<GITHUB_URL>>",
    "<<LINKEDIN_URL>>",
)


@pytest.fixture(scope="module")
def cv_template() -> str:
    return (
        importlib.resources.files("application_pipeline.latex") / "cv_template.tex"
    ).read_text(encoding="utf-8")


def test_cv_template_contains_no_identity_tokens(cv_template: str) -> None:
    leaked = [t for t in _IDENTITY_TOKENS if t in cv_template]
    assert leaked == [], f"cv_template.tex leaks identity tokens: {leaked}"


def test_latex_package_ships_vendored_moderncv_tree() -> None:
    """The whole moderncv 1.2.0 distro (per ADR-0034) ships with the package."""
    pkg = importlib.resources.files("application_pipeline.latex")
    actual = {item.name for item in pkg.iterdir() if not item.name.startswith("__")}
    missing = _EXPECTED_LATEX_PACKAGE_FILES - actual
    unexpected = actual - _EXPECTED_LATEX_PACKAGE_FILES
    assert missing == set(), f"missing vendored files: {missing}"
    assert unexpected == set(), f"unexpected files in latex package: {unexpected}"


def test_cv_template_makeletterclosing_uses_at_closing(cv_template: str) -> None:
    assert r"\@closing" in cv_template, (
        r"makeletterclosing override must use \@closing (not \closing)"
    )


def test_cv_template_resume_name_uses_my_macros(cv_template: str) -> None:
    """Resume body must use \\myFirstname/\\myFamilyname, not \\@firstname/\\@familyname.

    The \\ifdefstring{\\BUILD}{cover}{}{...} body is tokenised at outer scope
    where @ is "other"; \\@firstname inside it parses as \\@ (LaTeX's
    abbreviation-period macro) followed by letters, firing \\spacefactor in
    vertical mode. \\myFirstname/\\myFamilyname (from facts.tex) have no @.
    """
    assert r"\cvitem{Name}{\myFirstname{} \myFamilyname}" in cv_template
    resume_marker = r"\ifdefstring{\BUILD}{cover}{}{"
    idx = cv_template.find(resume_marker)
    assert idx != -1, "resume \\ifdefstring block not found"
    resume_body = cv_template[idx:]
    assert r"\@firstname" not in resume_body, (
        r"resume body uses \@firstname; tokenisation at outer-scope catcodes "
        r"breaks it. Use \myFirstname instead."
    )
    assert r"\@familyname" not in resume_body, (
        r"resume body uses \@familyname; tokenisation at outer-scope catcodes "
        r"breaks it. Use \myFamilyname instead."
    )


def test_cv_template_reduces_cover_stretch_in_order(cv_template: str) -> None:
    body = re.search(
        r"\\newcommand\{\\AutoCoverLetterStretch\}\[5\]\{(?P<body>.*?)\\unvbox",
        cv_template,
        re.DOTALL,
    )
    assert body is not None
    candidates = re.findall(
        r"\\SetCoverLetterBox\{#([1-4])\}\{#5\}", body.group("body")
    )
    assert candidates == ["1", "2", "3", "4"]
    assert body.group("body").count(r"\ifdim") == 3


def test_cv_template_cover_stretch_accepts_paragraph_slots(cv_template: str) -> None:
    assert r"\newcommand{\AutoCoverLetterStretch}" in cv_template
    assert r"\newcommand*{\AutoCoverLetterStretch}" not in cv_template
    assert r"\newcommand{\SetCoverLetterBox}" in cv_template
    assert r"\newcommand*{\SetCoverLetterBox}" not in cv_template


def test_cv_template_cover_gaps_track_selected_stretch(
    cv_template: str,
) -> None:
    assert (
        r"\xpatchcmd{\makelettertitle}{\@opening\\[1.5em]}{\@opening\\[\CoverLetterGap]}{}{}"
        in cv_template
    )
    assert (
        r"\makelettertitle"
        "\n"
        r"\setstretch{1.8}"
        "\n"
        r"\AutoCoverLetterStretch{1.8}{1.7}{1.6}{1.5}{%"
    ) in cv_template

    set_box = re.search(
        r"\\newcommand\{\\SetCoverLetterBox\}\[2\]\{(?P<body>.*?)\\makeletterclosing",
        cv_template,
        re.DOTALL,
    )
    assert set_box is not None
    assert r"\vspace{\CoverLetterGap}" not in set_box.group("body")
    assert r"\vspace{3em}" not in set_box.group("body")
    assert r"\vspace{\CoverLetterGap}\@closing" in cv_template
    assert r"\vspace{3em}\@closing" not in cv_template


def test_cv_template_hardcodes_cover_stretch_minimum_without_new_slot(
    cv_template: str,
    tmp_path: Path,
) -> None:
    assert r"\AutoCoverLetterStretch{1.8}{1.7}{1.6}{1.5}" in cv_template
    cv_tex = tmp_path / "cv.tex"
    cv_tex.write_text("%% SLOT: cover_stretch\n1.5\n", encoding="utf-8")

    with pytest.raises(slot_map.UnknownSlotError):
        slot_map.parse(cv_tex)
