"""Tests for the LaTeX template files — identity-token leak prevention."""

from __future__ import annotations

import importlib.resources

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
    "addressdisplay",
    "phonedisplay",
    "emaildisplay",
    "githubdisplay",
    "linkedindisplay",
)


@pytest.fixture(scope="module")
def cv_template() -> str:
    return (
        importlib.resources.files("application_pipeline.latex") / "cv_template.tex"
    ).read_text(encoding="utf-8")


def test_cv_template_contains_no_identity_tokens(cv_template: str) -> None:
    leaked = [t for t in _IDENTITY_TOKENS if t in cv_template]
    assert leaked == [], f"cv_template.tex leaks identity tokens: {leaked}"


@pytest.mark.parametrize("macro", _DISPLAY_MACROS)
def test_cv_template_reads_identity_via_display_macro(
    cv_template: str, macro: str
) -> None:
    assert rf"\{macro}" in cv_template
