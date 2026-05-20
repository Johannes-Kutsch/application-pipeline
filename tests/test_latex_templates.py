"""Tests for the LaTeX template files — identity-token leak prevention."""

from __future__ import annotations

import importlib.resources


def _latex_template_text(name: str) -> str:
    return (
        importlib.resources.files("application_pipeline.templates") / "latex" / name
    ).read_text(encoding="utf-8")


def _user_info_template_text(name: str) -> str:
    return (
        importlib.resources.files("application_pipeline.templates") / "user-info" / name
    ).read_text(encoding="utf-8")


_IDENTITY_TOKENS = (
    "<<ADDRESS_STREET>>",
    "<<ADDRESS_CITY>>",
    "<<PHONE>>",
    "<<EMAIL>>",
    "<<GITHUB_URL>>",
    "<<LINKEDIN_URL>>",
)

_DISPLAY_MACROS = (
    (r"\addressdisplay", "<<ADDRESS_STREET>>", "<<ADDRESS_CITY>>"),
    (r"\phonedisplay", "<<PHONE>>"),
    (r"\emaildisplay", "<<EMAIL>>"),
    (r"\githubdisplay", "<<GITHUB_URL>>"),
    (r"\linkedindisplay", "<<LINKEDIN_URL>>"),
)

_MODERNCV_SETTERS = (
    (r"\address{<<ADDRESS_STREET>>}", "<<ADDRESS_CITY>>"),
    (r"\phone[mobile]{<<PHONE>>}",),
    (r"\email{<<EMAIL>>}",),
    (r"\social[github]{<<GITHUB_URL>>}",),
    (r"\social[linkedin]{<<LINKEDIN_URL>>}",),
)


def test_cv_template_contains_no_identity_tokens() -> None:
    text = _latex_template_text("cv_template.tex")
    leaked = [t for t in _IDENTITY_TOKENS if t in text]
    assert leaked == [], f"cv_template.tex leaks identity tokens: {leaked}"


def test_contact_tex_defines_addressdisplay_macro() -> None:
    text = _user_info_template_text("contact.tex")
    assert r"\def\addressdisplay" in text


def test_contact_tex_defines_phonedisplay_macro() -> None:
    text = _user_info_template_text("contact.tex")
    assert r"\def\phonedisplay" in text


def test_contact_tex_defines_emaildisplay_macro() -> None:
    text = _user_info_template_text("contact.tex")
    assert r"\def\emaildisplay" in text


def test_contact_tex_defines_githubdisplay_macro() -> None:
    text = _user_info_template_text("contact.tex")
    assert r"\def\githubdisplay" in text


def test_contact_tex_defines_linkedindisplay_macro() -> None:
    text = _user_info_template_text("contact.tex")
    assert r"\def\linkedindisplay" in text


def test_contact_tex_addressdisplay_expands_to_token() -> None:
    text = _user_info_template_text("contact.tex")
    assert "<<ADDRESS_STREET>>" in text
    assert "<<ADDRESS_CITY>>" in text


def test_contact_tex_phonedisplay_expands_to_token() -> None:
    text = _user_info_template_text("contact.tex")
    assert "<<PHONE>>" in text


def test_contact_tex_emaildisplay_expands_to_token() -> None:
    text = _user_info_template_text("contact.tex")
    assert "<<EMAIL>>" in text


def test_contact_tex_githubdisplay_expands_to_token() -> None:
    text = _user_info_template_text("contact.tex")
    assert "<<GITHUB_URL>>" in text


def test_contact_tex_linkedindisplay_expands_to_token() -> None:
    text = _user_info_template_text("contact.tex")
    assert "<<LINKEDIN_URL>>" in text


def test_contact_tex_retains_moderncv_address_setter() -> None:
    text = _user_info_template_text("contact.tex")
    assert r"\address{<<ADDRESS_STREET>>}" in text
    assert "<<ADDRESS_CITY>>" in text


def test_contact_tex_retains_moderncv_phone_setter() -> None:
    text = _user_info_template_text("contact.tex")
    assert r"\phone[mobile]{<<PHONE>>}" in text


def test_contact_tex_retains_moderncv_email_setter() -> None:
    text = _user_info_template_text("contact.tex")
    assert r"\email{<<EMAIL>>}" in text


def test_contact_tex_retains_moderncv_github_setter() -> None:
    text = _user_info_template_text("contact.tex")
    assert r"\social[github]{<<GITHUB_URL>>}" in text


def test_contact_tex_retains_moderncv_linkedin_setter() -> None:
    text = _user_info_template_text("contact.tex")
    assert r"\social[linkedin]{<<LINKEDIN_URL>>}" in text


def test_cv_template_uses_addressdisplay_in_personal_section() -> None:
    text = _latex_template_text("cv_template.tex")
    assert r"\addressdisplay" in text


def test_cv_template_uses_phonedisplay_in_personal_section() -> None:
    text = _latex_template_text("cv_template.tex")
    assert r"\phonedisplay" in text


def test_cv_template_uses_emaildisplay_in_personal_section() -> None:
    text = _latex_template_text("cv_template.tex")
    assert r"\emaildisplay" in text


def test_cv_template_uses_githubdisplay_in_personal_section() -> None:
    text = _latex_template_text("cv_template.tex")
    assert r"\githubdisplay" in text


def test_cv_template_uses_linkedindisplay_in_personal_section() -> None:
    text = _latex_template_text("cv_template.tex")
    assert r"\linkedindisplay" in text
