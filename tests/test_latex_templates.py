"""Tests for the LaTeX template files — identity-token leak prevention."""

from __future__ import annotations

import importlib.resources
import re

import pytest

_IDENTITY_TOKENS = (
    "<<ADDRESS_STREET>>",
    "<<ADDRESS_CITY>>",
    "<<PHONE>>",
    "<<EMAIL>>",
    "<<GITHUB_URL>>",
    "<<LINKEDIN_URL>>",
)

_DISPLAY_MACROS = (
    ("addressdisplay", "<<ADDRESS_STREET>>, <<ADDRESS_CITY>>"),
    ("phonedisplay", "<<PHONE>>"),
    ("emaildisplay", "<<EMAIL>>"),
    ("githubdisplay", "<<GITHUB_URL>>"),
    ("linkedindisplay", "<<LINKEDIN_URL>>"),
)

_MODERNCV_SETTERS = (
    r"\address{<<ADDRESS_STREET>>}{<<ADDRESS_CITY>>}{}",
    r"\phone[mobile]{<<PHONE>>}",
    r"\email{<<EMAIL>>}",
    r"\social[github]{<<GITHUB_URL>>}",
    r"\social[linkedin]{<<LINKEDIN_URL>>}",
)


@pytest.fixture(scope="module")
def cv_template() -> str:
    return (
        importlib.resources.files("application_pipeline.templates")
        / "latex"
        / "cv_template.tex"
    ).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def contact_seed() -> str:
    return (
        importlib.resources.files("application_pipeline.templates")
        / "user-info"
        / "contact.tex"
    ).read_text(encoding="utf-8")


def test_cv_template_contains_no_identity_tokens(cv_template: str) -> None:
    leaked = [t for t in _IDENTITY_TOKENS if t in cv_template]
    assert leaked == [], f"cv_template.tex leaks identity tokens: {leaked}"


@pytest.mark.parametrize(("macro", "body"), _DISPLAY_MACROS)
def test_contact_seed_defines_display_macro(
    contact_seed: str, macro: str, body: str
) -> None:
    match = re.search(rf"\\def\\{macro}\{{(.*?)\}}", contact_seed)
    assert match is not None, f"\\def\\{macro} not found in contact.tex"
    assert match.group(1) == body


@pytest.mark.parametrize("setter", _MODERNCV_SETTERS)
def test_contact_seed_retains_moderncv_setter(contact_seed: str, setter: str) -> None:
    assert setter in contact_seed


@pytest.mark.parametrize("macro", [m for m, _ in _DISPLAY_MACROS])
def test_cv_template_reads_identity_via_display_macro(
    cv_template: str, macro: str
) -> None:
    assert rf"\{macro}" in cv_template
