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
from application_pipeline.prompts import (
    CLASSIFY_RELEVANCE_V2_SLOTS,
    JUDGE_TOP_N_V2_SLOTS,
)
from application_pipeline.search_terms.types import SearchTerms


_EMPTY_SEARCH_TERMS = SearchTerms(
    keywords=("python",), skills=("Python", "Go"), negative_keywords=()
)


REQUIRED_BODY = """
from application_pipeline import SourceEntry

KEYWORDS = ["python"]
SKILLS = ["python"]
SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
LOCATIONS = ["Hamburg"]
"""


def make_config_with_user_info(tmp_path: pathlib.Path) -> Config:
    user_info = tmp_path / "user-info"
    user_info.mkdir(exist_ok=True)
    triage = user_info / "triage-profile"
    triage.mkdir()
    (triage / "self-description.md").write_text("I am a developer\n")
    (triage / "match-criteria.md").write_text("Hamburg, remote\n")
    return Config(
        sources=[SourceEntry(parser_type="bundesagentur")],
        locations=["Hamburg"],
        user_info_dir=user_info,
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
    raw = "  {ITEMS}\n"
    tpl = PromptTemplate(raw, frozenset({"ITEMS"}))

    assert tpl.template == raw
    assert tpl.template is raw


# --- load_prompts: package-resource templates + user-info injection ---


def test_load_prompts_returns_prompt_template_per_call_site(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config, _EMPTY_SEARCH_TERMS)

    assert isinstance(prompts.classify_relevance_v2, PromptTemplate)
    assert isinstance(prompts.judge_top_n_v2, PromptTemplate)


def test_load_prompts_classify_embeds_both_named_sub_blocks(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config, _EMPTY_SEARCH_TERMS)
    rendered = prompts.classify_relevance_v2.render(
        LISTING_BULLETS="- Jobtitel: x", RAW_DESCRIPTION="y"
    )

    assert "# Kandidatenprofil" in rendered
    assert "# Match-Kriterien" in rendered
    assert "I am a developer" in rendered
    assert "Hamburg, remote" in rendered


def test_load_prompts_judge_embeds_both_named_sub_blocks(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config, _EMPTY_SEARCH_TERMS)
    rendered = prompts.judge_top_n_v2.render(CANDIDATES="x")

    assert "# Kandidatenprofil" in rendered
    assert "# Match-Kriterien" in rendered
    assert "I am a developer" in rendered
    assert "Hamburg, remote" in rendered


def test_load_prompts_classify_contains_verdict_tag_instruction(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config, _EMPTY_SEARCH_TERMS)
    rendered = prompts.classify_relevance_v2.render(
        LISTING_BULLETS="- Jobtitel: x", RAW_DESCRIPTION="y"
    )

    assert "<verdict>" in rendered


# --- load_prompts: legacy / missing / empty user-info files ---


def test_load_prompts_raises_when_legacy_domain_fit_present(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)
    (config.user_info_dir / "triage-profile" / "domain-fit.md").write_text("legacy\n")

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config, _EMPTY_SEARCH_TERMS)
    assert "domain-fit.md" in str(exc_info.value)
    assert "match-criteria.md" in str(exc_info.value)


@pytest.mark.parametrize(
    "missing_file",
    ["self-description.md", "match-criteria.md"],
)
def test_load_prompts_raises_when_user_info_file_missing(
    tmp_path: pathlib.Path, missing_file: str
) -> None:
    config = make_config_with_user_info(tmp_path)
    (config.user_info_dir / "triage-profile" / missing_file).unlink()

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config, _EMPTY_SEARCH_TERMS)
    assert missing_file in str(exc_info.value)


@pytest.mark.parametrize(
    "empty_file",
    ["self-description.md", "match-criteria.md"],
)
def test_load_prompts_raises_when_user_info_file_empty(
    tmp_path: pathlib.Path, empty_file: str
) -> None:
    config = make_config_with_user_info(tmp_path)
    (config.user_info_dir / "triage-profile" / empty_file).write_text("")

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config, _EMPTY_SEARCH_TERMS)
    assert empty_file in str(exc_info.value)


# --- load_prompts: via load() ---


def test_load_prompts_via_load(tmp_path: pathlib.Path) -> None:
    user_info = tmp_path / "user-info"
    user_info.mkdir()
    triage = user_info / "triage-profile"
    triage.mkdir()
    (triage / "self-description.md").write_text("background\n")
    (triage / "match-criteria.md").write_text("Hamburg\n")
    path = tmp_path / "config.py"
    path.write_text(REQUIRED_BODY)

    config = load(path)
    prompts = load_prompts(config, _EMPTY_SEARCH_TERMS)

    assert isinstance(prompts.classify_relevance_v2, PromptTemplate)
    assert isinstance(prompts.judge_top_n_v2, PromptTemplate)


# --- Prompts dataclass ---


def test_prompts_is_frozen() -> None:
    tpl = PromptTemplate(
        "{LISTING_BULLETS} {RAW_DESCRIPTION}",
        CLASSIFY_RELEVANCE_V2_SLOTS,
    )
    prompts = Prompts(
        classify_relevance_v2=tpl,
        judge_top_n_v2=PromptTemplate("{CANDIDATES}", JUDGE_TOP_N_V2_SLOTS),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        prompts.classify_relevance_v2 = tpl  # type: ignore[misc]


# --- error hierarchy ---


def test_prompt_error_is_not_user_settings_error() -> None:
    from application_pipeline import UserSettingsError

    assert not issubclass(PromptError, UserSettingsError)


def test_prompt_error_is_exception() -> None:
    assert issubclass(PromptError, Exception)
