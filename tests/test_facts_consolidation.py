"""Tests for issue #460 — user-info consolidates to facts.tex."""

from __future__ import annotations

import importlib.resources

import pytest


_FACTS_DEFS = (
    r"\def\myFirstname",
    r"\def\myFamilyname",
    r"\def\myStreet",
    r"\def\myZip",
    r"\def\myPhone",
    r"\def\myEmail",
    r"\def\myGithub",
    r"\def\myLinkedin",
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


@pytest.fixture(scope="module")
def facts_seed() -> str:
    return (
        importlib.resources.files("application_pipeline.templates")
        / "user-info"
        / "facts.tex"
    ).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cv_template() -> str:
    return (
        importlib.resources.files("application_pipeline.latex") / "cv_template.tex"
    ).read_text(encoding="utf-8")


# --- facts.tex presence and content ---


def test_facts_tex_exists_in_package(facts_seed: str) -> None:
    assert len(facts_seed) > 0


@pytest.mark.parametrize("def_line", _FACTS_DEFS)
def test_facts_tex_defines_each_fact(facts_seed: str, def_line: str) -> None:
    assert def_line in facts_seed, f"{def_line} not found in facts.tex"


@pytest.mark.parametrize("call", _MODERNCV_CALLS)
def test_facts_tex_contains_no_moderncv_calls(facts_seed: str, call: str) -> None:
    assert call not in facts_seed, f"facts.tex must not contain moderncv call {call!r}"


# --- identity.tex and contact.tex deleted from package ---


def test_identity_tex_absent_from_package() -> None:
    user_info = (
        importlib.resources.files("application_pipeline.templates") / "user-info"
    )
    names = {item.name for item in user_info.iterdir()}
    assert "identity.tex" not in names, "identity.tex must be deleted from package"


def test_contact_tex_absent_from_package() -> None:
    user_info = (
        importlib.resources.files("application_pipeline.templates") / "user-info"
    )
    names = {item.name for item in user_info.iterdir()}
    assert "contact.tex" not in names, "contact.tex must be deleted from package"


# --- cv_template.tex absorbs moderncv bridges ---


def test_cv_template_inputs_facts(cv_template: str) -> None:
    assert r"\input{\UserDataDir/facts}" in cv_template


def test_cv_template_does_not_input_identity(cv_template: str) -> None:
    assert r"\input{\UserDataDir/identity}" not in cv_template


def test_cv_template_does_not_input_contact(cv_template: str) -> None:
    assert r"\input{\UserDataDir/contact}" not in cv_template


def test_cv_template_contains_firstname_bridge(cv_template: str) -> None:
    assert r"\firstname{\myFirstname}" in cv_template


def test_cv_template_contains_familyname_bridge(cv_template: str) -> None:
    assert r"\familyname{\myFamilyname}" in cv_template


def test_cv_template_contains_address_bridge(cv_template: str) -> None:
    assert r"\address{\myStreet}{\myZip}{}" in cv_template


def test_cv_template_contains_phone_bridge(cv_template: str) -> None:
    assert r"\phone[mobile]{\myPhone}" in cv_template


def test_cv_template_contains_email_bridge(cv_template: str) -> None:
    assert r"\email{\myEmail}" in cv_template


def test_cv_template_contains_github_bridge(cv_template: str) -> None:
    assert r"\social[github]{\myGithub}" in cv_template


def test_cv_template_contains_linkedin_bridge(cv_template: str) -> None:
    assert r"\social[linkedin]{\myLinkedin}" in cv_template


def test_cv_template_contains_title(cv_template: str) -> None:
    assert r"\title{Lebenslauf}" in cv_template


def test_cv_template_contains_photo(cv_template: str) -> None:
    assert r"\photo[120pt][1pt]{\UserDataDir/profile}" in cv_template


def test_cv_template_defines_display_macros(cv_template: str) -> None:
    assert r"\def\addressdisplay" in cv_template
    assert r"\def\phonedisplay" in cv_template
    assert r"\def\emaildisplay" in cv_template
    assert r"\def\githubdisplay" in cv_template
    assert r"\def\linkedindisplay" in cv_template


# --- languages and hobbies render from \Languages and \Hobbies ---


def test_cv_template_references_languages(cv_template: str) -> None:
    assert r"\Languages" in cv_template


def test_cv_template_references_hobbies(cv_template: str) -> None:
    assert r"\Hobbies" in cv_template


def test_cv_template_no_languages_block_marker(cv_template: str) -> None:
    assert "<<LANGUAGES_BLOCK>>" not in cv_template


def test_cv_template_no_hobbies_block_marker(cv_template: str) -> None:
    assert "<<HOBBIES_BLOCK>>" not in cv_template
