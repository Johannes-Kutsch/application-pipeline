"""Tests for issue #460 — user-info consolidates to facts.tex."""

from __future__ import annotations

import importlib.resources

import pytest


_FACTS_DEFS = (
    r"\def\myFirstname",
    r"\def\myFamilyname",
    r"\def\myCity",
    r"\def\PersonalInfo",
    r"\def\Languages",
    r"\def\Hobbies",
)

_MODERNCV_CALLS = (
    r"\firstname",
    r"\familyname",
    r"\address",
    r"\phone",
    r"\email",
    r"\social",
)

_CV_TEMPLATE_BRIDGES = (
    r"\input{\CvDataDir/facts}",
    r"\firstname{\myFirstname}",
    r"\familyname{\myFamilyname}",
    # Identity surfaces in the resume body via \PersonalInfo (defined in
    # facts.tex), not via moderncv's \address / \phone / \email calls — those
    # render the casual-style letter footer on every cover-letter page.
    r"\title{Lebenslauf}",
    r"\photo[120pt][1pt]{\CvDataDir/profile}",
    r"\PersonalInfo",
)

_RETIRED_FILENAMES = ("identity.tex", "contact.tex")


@pytest.fixture(scope="module")
def facts_seed() -> str:
    return (
        importlib.resources.files("application_pipeline.templates")
        / "application-pipeline"
        / "user-info"
        / "cv"
        / "facts.tex"
    ).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cv_template() -> str:
    return (
        importlib.resources.files("application_pipeline.latex") / "cv_template.tex"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("def_line", _FACTS_DEFS)
def test_facts_tex_defines_each_fact(facts_seed: str, def_line: str) -> None:
    assert def_line in facts_seed, f"{def_line} not found in facts.tex"


@pytest.mark.parametrize("call", _MODERNCV_CALLS)
def test_facts_tex_contains_no_moderncv_calls(facts_seed: str, call: str) -> None:
    assert call not in facts_seed, f"facts.tex must not contain moderncv call {call!r}"


@pytest.mark.parametrize("retired", _RETIRED_FILENAMES)
def test_retired_user_info_file_absent_from_package(retired: str) -> None:
    user_info_cv = (
        importlib.resources.files("application_pipeline.templates")
        / "application-pipeline"
        / "user-info"
        / "cv"
    )
    names = {item.name for item in user_info_cv.iterdir()}
    assert retired not in names, f"{retired} must be deleted from package"


@pytest.mark.parametrize("retired", _RETIRED_FILENAMES)
def test_cv_template_does_not_input_retired_file(
    cv_template: str, retired: str
) -> None:
    stem = retired.removesuffix(".tex")
    assert rf"\input{{\CvDataDir/{stem}}}" not in cv_template


@pytest.mark.parametrize("bridge", _CV_TEMPLATE_BRIDGES)
def test_cv_template_contains_bridge(cv_template: str, bridge: str) -> None:
    assert bridge in cv_template, f"{bridge!r} missing from cv_template.tex"


def test_cv_template_references_languages_and_hobbies(cv_template: str) -> None:
    assert r"\Languages" in cv_template
    assert r"\Hobbies" in cv_template


@pytest.mark.parametrize("marker", ("<<LANGUAGES_BLOCK>>", "<<HOBBIES_BLOCK>>"))
def test_cv_template_no_retired_block_marker(cv_template: str, marker: str) -> None:
    assert marker not in cv_template
