import dataclasses
import pathlib

import pytest

from application_pipeline import (
    Config,
    PromptError,
    PromptTemplate,
    Prompts,
    SourceEntry,
    load,
    load_prompts,
)
from application_pipeline.prompts import CLASSIFY_RELEVANCE_SLOTS, JUDGE_MATCH_SLOTS


REQUIRED_BODY = """
from application_pipeline import SourceEntry

KEYWORDS = ["python"]
SKILLS = ["python"]
SOURCES = [SourceEntry(parser_type="bundesagentur")]
LOCATIONS = ["Hamburg"]
"""

_CLASSIFY_DE = "Klassifiziere: Titel={title} Beschreibung={raw_description}\n"
_CLASSIFY_EN = "Classify: title={title} description={raw_description}\n"
_JUDGE_DE = "Beurteile: Fähigkeiten={skills} Beschreibung={raw_description}\n"
_JUDGE_EN = "Judge: skills={skills} description={raw_description}\n"


def write_prompts(
    prompts_dir: pathlib.Path,
    *,
    classify_de: str = _CLASSIFY_DE,
    classify_en: str = _CLASSIFY_EN,
    judge_de: str = _JUDGE_DE,
    judge_en: str = _JUDGE_EN,
) -> None:
    prompts_dir.mkdir(exist_ok=True)
    (prompts_dir / "classify_relevance.de.md").write_text(classify_de, encoding="utf-8")
    (prompts_dir / "classify_relevance.en.md").write_text(classify_en, encoding="utf-8")
    (prompts_dir / "judge_match.de.md").write_text(judge_de, encoding="utf-8")
    (prompts_dir / "judge_match.en.md").write_text(judge_en, encoding="utf-8")


def make_config(tmp_path: pathlib.Path) -> Config:
    return Config(
        keywords=["k"],
        skills=[],
        sources=[SourceEntry(parser_type="bundesagentur")],
        locations=["Hamburg"],
        prompts_dir=tmp_path / "prompts",
    )


# --- PromptTemplate.render() ---


def test_prompt_template_render_substitutes_slots() -> None:
    tpl = PromptTemplate("Hello {name}!", frozenset({"name"}))

    assert tpl.render(name="world") == "Hello world!"


def test_prompt_template_render_substitutes_repeated_slot() -> None:
    tpl = PromptTemplate("{x} and {x} again", frozenset({"x"}))

    assert tpl.render(x="foo") == "foo and foo again"


def test_prompt_template_render_raises_on_missing_slot() -> None:
    tpl = PromptTemplate("{a} {b}", frozenset({"a", "b"}))

    with pytest.raises(PromptError, match="missing"):
        tpl.render(a="only-a")


def test_prompt_template_render_raises_on_unknown_slot() -> None:
    tpl = PromptTemplate("{a}", frozenset({"a"}))

    with pytest.raises(PromptError, match="unknown"):
        tpl.render(a="val", extra="oops")


def test_prompt_template_preserves_template_verbatim() -> None:
    raw = "  {title}\n{raw_description}  "
    tpl = PromptTemplate(raw, frozenset({"title", "raw_description"}))

    assert tpl.template == raw
    assert tpl.template is raw


# --- load_prompts: happy path ---


def test_load_prompts_returns_per_language_prompt_templates(
    tmp_path: pathlib.Path,
) -> None:
    write_prompts(tmp_path / "prompts")
    config = make_config(tmp_path)

    prompts = load_prompts(config)

    assert isinstance(prompts, Prompts)
    assert isinstance(prompts.classify_relevance["de"], PromptTemplate)
    assert isinstance(prompts.classify_relevance["en"], PromptTemplate)
    assert isinstance(prompts.judge_match["de"], PromptTemplate)
    assert isinstance(prompts.judge_match["en"], PromptTemplate)


def test_load_prompts_preserves_template_string(tmp_path: pathlib.Path) -> None:
    write_prompts(tmp_path / "prompts")
    config = make_config(tmp_path)

    prompts = load_prompts(config)

    assert prompts.classify_relevance["de"].template == _CLASSIFY_DE
    assert prompts.classify_relevance["en"].template == _CLASSIFY_EN
    assert prompts.judge_match["de"].template == _JUDGE_DE
    assert prompts.judge_match["en"].template == _JUDGE_EN


def test_load_prompts_preserves_utf8(tmp_path: pathlib.Path) -> None:
    classify_de = "Klassifiziere — Schlüssel: ✓ {title} {raw_description}\n"
    classify_en = "Classify — key: ✓ {title} {raw_description}\n"
    judge_de = "Beurteile — Fähigkeiten: π {skills} {raw_description}\n"
    judge_en = "Judge — skills: π {skills} {raw_description}\n"
    write_prompts(
        tmp_path / "prompts",
        classify_de=classify_de,
        classify_en=classify_en,
        judge_de=judge_de,
        judge_en=judge_en,
    )
    config = make_config(tmp_path)

    prompts = load_prompts(config)

    assert prompts.classify_relevance["de"].template == classify_de
    assert prompts.classify_relevance["en"].template == classify_en
    assert prompts.judge_match["de"].template == judge_de
    assert prompts.judge_match["en"].template == judge_en


def test_load_prompts_strips_utf8_bom(tmp_path: pathlib.Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    # Write with utf-8-sig so the file starts with BOM bytes (\xef\xbb\xbf).
    (prompts_dir / "classify_relevance.de.md").write_text(
        "{title} {raw_description}\n", encoding="utf-8-sig"
    )
    (prompts_dir / "classify_relevance.en.md").write_text(
        _CLASSIFY_EN, encoding="utf-8"
    )
    (prompts_dir / "judge_match.de.md").write_text(_JUDGE_DE, encoding="utf-8")
    (prompts_dir / "judge_match.en.md").write_text(_JUDGE_EN, encoding="utf-8")
    config = make_config(tmp_path)

    prompts = load_prompts(config)

    assert not prompts.classify_relevance["de"].template.startswith("﻿")
    assert prompts.classify_relevance["de"].template.startswith("{title}")


def test_prompts_is_frozen() -> None:
    tpl = PromptTemplate("{title} {raw_description}", CLASSIFY_RELEVANCE_SLOTS)
    prompts = Prompts(
        classify_relevance={"de": tpl, "en": tpl},
        judge_match={
            "de": PromptTemplate("{skills} {raw_description}", JUDGE_MATCH_SLOTS),
            "en": PromptTemplate("{skills} {raw_description}", JUDGE_MATCH_SLOTS),
        },
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        prompts.classify_relevance = {}  # type: ignore[misc]


def test_load_prompts_via_load(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "config.py"
    path.write_text(REQUIRED_BODY)
    write_prompts(tmp_path / "prompts")

    config = load(path)
    prompts = load_prompts(config)

    assert "de" in prompts.classify_relevance
    assert "en" in prompts.classify_relevance


# --- load_prompts: missing / empty files ---


@pytest.mark.parametrize(
    "missing_file",
    [
        "classify_relevance.de.md",
        "classify_relevance.en.md",
        "judge_match.de.md",
        "judge_match.en.md",
    ],
)
def test_load_prompts_raises_when_file_missing(
    tmp_path: pathlib.Path, missing_file: str
) -> None:
    write_prompts(tmp_path / "prompts")
    (tmp_path / "prompts" / missing_file).unlink()
    config = make_config(tmp_path)

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config)
    assert missing_file in str(exc_info.value)


@pytest.mark.parametrize(
    "empty_file",
    [
        "classify_relevance.de.md",
        "classify_relevance.en.md",
        "judge_match.de.md",
        "judge_match.en.md",
    ],
)
def test_load_prompts_raises_when_file_empty(
    tmp_path: pathlib.Path, empty_file: str
) -> None:
    write_prompts(tmp_path / "prompts")
    (tmp_path / "prompts" / empty_file).write_text("")
    config = make_config(tmp_path)

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config)
    assert empty_file in str(exc_info.value)


# --- load_prompts: slot validation ---


@pytest.mark.parametrize("lang", ["de", "en"])
def test_load_prompts_raises_on_missing_classify_slot(
    tmp_path: pathlib.Path, lang: str
) -> None:
    prompts_dir = tmp_path / "prompts"
    write_prompts(prompts_dir)
    (prompts_dir / f"classify_relevance.{lang}.md").write_text(
        "No slots here\n", encoding="utf-8"
    )
    config = make_config(tmp_path)

    with pytest.raises(PromptError, match="missing slots"):
        load_prompts(config)


@pytest.mark.parametrize("lang", ["de", "en"])
def test_load_prompts_raises_on_unknown_classify_slot(
    tmp_path: pathlib.Path, lang: str
) -> None:
    prompts_dir = tmp_path / "prompts"
    write_prompts(prompts_dir)
    (prompts_dir / f"classify_relevance.{lang}.md").write_text(
        "{title} {raw_description} {extra}\n", encoding="utf-8"
    )
    config = make_config(tmp_path)

    with pytest.raises(PromptError, match="unknown slots"):
        load_prompts(config)


@pytest.mark.parametrize("lang", ["de", "en"])
def test_load_prompts_raises_on_missing_judge_slot(
    tmp_path: pathlib.Path, lang: str
) -> None:
    prompts_dir = tmp_path / "prompts"
    write_prompts(prompts_dir)
    (prompts_dir / f"judge_match.{lang}.md").write_text(
        "No slots here\n", encoding="utf-8"
    )
    config = make_config(tmp_path)

    with pytest.raises(PromptError, match="missing slots"):
        load_prompts(config)


@pytest.mark.parametrize("lang", ["de", "en"])
def test_load_prompts_raises_on_unknown_judge_slot(
    tmp_path: pathlib.Path, lang: str
) -> None:
    prompts_dir = tmp_path / "prompts"
    write_prompts(prompts_dir)
    (prompts_dir / f"judge_match.{lang}.md").write_text(
        "{skills} {raw_description} {bogus}\n", encoding="utf-8"
    )
    config = make_config(tmp_path)

    with pytest.raises(PromptError, match="unknown slots"):
        load_prompts(config)


# --- load_prompts: format spec / conversion flag rejection ---


@pytest.mark.parametrize("lang", ["de", "en"])
def test_load_prompts_raises_on_format_spec(tmp_path: pathlib.Path, lang: str) -> None:
    prompts_dir = tmp_path / "prompts"
    write_prompts(prompts_dir)
    (prompts_dir / f"classify_relevance.{lang}.md").write_text(
        "{title:>10} {raw_description}\n", encoding="utf-8"
    )
    config = make_config(tmp_path)

    with pytest.raises(PromptError, match="format spec"):
        load_prompts(config)


@pytest.mark.parametrize("lang", ["de", "en"])
def test_load_prompts_raises_on_conversion_flag(
    tmp_path: pathlib.Path, lang: str
) -> None:
    prompts_dir = tmp_path / "prompts"
    write_prompts(prompts_dir)
    (prompts_dir / f"classify_relevance.{lang}.md").write_text(
        "{title!r} {raw_description}\n", encoding="utf-8"
    )
    config = make_config(tmp_path)

    with pytest.raises(PromptError, match="conversion flag"):
        load_prompts(config)


# --- load_prompts: malformed format string ---


@pytest.mark.parametrize("lang", ["de", "en"])
def test_load_prompts_raises_on_malformed_format_string(
    tmp_path: pathlib.Path, lang: str
) -> None:
    prompts_dir = tmp_path / "prompts"
    write_prompts(prompts_dir)
    (prompts_dir / f"classify_relevance.{lang}.md").write_text(
        "{title} {raw_description} {unclosed\n", encoding="utf-8"
    )
    config = make_config(tmp_path)

    with pytest.raises(PromptError):
        load_prompts(config)


# --- error hierarchy ---


def test_prompt_error_is_not_user_settings_error() -> None:
    from application_pipeline import UserSettingsError

    assert not issubclass(PromptError, UserSettingsError)


def test_prompt_error_is_exception() -> None:
    assert issubclass(PromptError, Exception)


# --- error message shape ---


def test_load_prompts_error_message_contains_path(tmp_path: pathlib.Path) -> None:
    write_prompts(tmp_path / "prompts")
    (tmp_path / "prompts" / "classify_relevance.de.md").unlink()
    config = make_config(tmp_path)

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config)

    assert str(tmp_path / "prompts" / "classify_relevance.de.md") in str(exc_info.value)
