"""Tests for the LaTeX template files — identity-token leak prevention."""

from __future__ import annotations

import importlib.resources

import pytest

_EXPECTED_LATEX_PACKAGE_FILES = frozenset(
    {"cv_template.tex", "slot_map.py", "__init__.py"}
)

_VENDORED_SUFFIXES = frozenset({".cls", ".sty"})

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


def test_latex_package_contains_no_vendored_cls_or_sty_files() -> None:
    pkg = importlib.resources.files("application_pipeline.latex")
    vendored = [
        item.name
        for item in pkg.iterdir()
        if any(item.name.endswith(sfx) for sfx in _VENDORED_SUFFIXES)
    ]
    assert vendored == [], f"vendored files must be deleted: {vendored}"


def test_latex_package_contains_only_expected_files() -> None:
    pkg = importlib.resources.files("application_pipeline.latex")
    actual = {item.name for item in pkg.iterdir() if not item.name.startswith("__")}
    unexpected = actual - _EXPECTED_LATEX_PACKAGE_FILES
    assert unexpected == set(), f"unexpected files in latex package: {unexpected}"


def test_cv_template_has_version_guard(cv_template: str) -> None:
    assert r"\@ifclasslater{moderncv}" in cv_template, (
        "cv_template.tex must include a \\@ifclasslater version guard"
    )


def test_cv_template_makeletterclosing_uses_at_closing(cv_template: str) -> None:
    assert r"\@closing" in cv_template, (
        r"makeletterclosing override must use \@closing (not \closing)"
    )


def test_cv_template_cventry_patch_is_wrapped_in_atbegindocument(
    cv_template: str,
) -> None:
    assert r"\AtBeginDocument" in cv_template, (
        "trailing-dot xpatch must be wrapped in \\AtBeginDocument for v2.x compatibility"
    )
