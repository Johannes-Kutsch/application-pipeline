import dataclasses
import pathlib

import pytest

from application_pipeline import (
    Config,
    PromptError,
    PromptTemplate,
    Prompts,
    SplitPromptTemplate,
    SourceEntry,
    load,
    load_prompts,
)
from application_pipeline.prompts import CLASSIFY_RELEVANCE_SLOTS, JUDGE_MATCH_SLOTS


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
    (triage / "domain-fit.md").write_text("ML roles\n")
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


def test_load_prompts_returns_single_template_per_call_site(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)

    assert isinstance(prompts.classify_relevance, SplitPromptTemplate)
    assert isinstance(prompts.judge_match, PromptTemplate)


def test_load_prompts_embeds_user_info_in_classify_prompt(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)
    rendered = prompts.classify_relevance.render_system()

    assert "<user-info>" in rendered
    assert "I am a developer" in rendered
    assert "ML roles" in rendered


def test_load_prompts_embeds_user_info_in_judge_prompt(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)
    rendered = prompts.judge_match.render(skills="Python", raw_description="some job")

    assert "<user-info>" in rendered
    assert "I am a developer" in rendered
    assert "Hamburg, remote" in rendered


def test_load_prompts_classify_does_not_embed_match_criteria(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)
    rendered = prompts.classify_relevance.render_system()

    assert "Hamburg, remote" not in rendered


def test_load_prompts_judge_does_not_embed_domain_fit(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)
    rendered = prompts.judge_match.render(skills="Python", raw_description="some job")

    assert "ML roles" not in rendered


def test_load_prompts_classify_contains_verdicts_tag_instruction(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)
    rendered = prompts.classify_relevance.render_system()

    assert "<verdict>" in rendered


def test_load_prompts_judge_contains_verdict_tag_instruction(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)
    rendered = prompts.judge_match.render(skills="Python", raw_description="some job")

    assert "<verdict>" in rendered


# --- load_prompts: missing / empty user-info files ---


@pytest.mark.parametrize(
    "missing_file",
    ["self-description.md", "domain-fit.md", "match-criteria.md"],
)
def test_load_prompts_raises_when_user_info_file_missing(
    tmp_path: pathlib.Path, missing_file: str
) -> None:
    config = make_config_with_user_info(tmp_path)
    (config.user_info_dir / "triage-profile" / missing_file).unlink()

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config)
    assert missing_file in str(exc_info.value)


@pytest.mark.parametrize(
    "empty_file",
    ["self-description.md", "domain-fit.md", "match-criteria.md"],
)
def test_load_prompts_raises_when_user_info_file_empty(
    tmp_path: pathlib.Path, empty_file: str
) -> None:
    config = make_config_with_user_info(tmp_path)
    (config.user_info_dir / "triage-profile" / empty_file).write_text("")

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config)
    assert empty_file in str(exc_info.value)


# --- load_prompts: via load() ---


def test_load_prompts_via_load(tmp_path: pathlib.Path) -> None:
    user_info = tmp_path / "user-info"
    user_info.mkdir()
    triage = user_info / "triage-profile"
    triage.mkdir()
    (triage / "self-description.md").write_text("background\n")
    (triage / "domain-fit.md").write_text("ML roles\n")
    (triage / "match-criteria.md").write_text("Hamburg\n")
    (tmp_path / "layout.py").write_text(
        "PLACEHOLDER_GROUPS = {}\n"
        'CARD_TEMPLATE = "# {rank} \xb7 {title}\\n\\n{summary}\\n\\n---\\n<{url}>\\n"\n'
    )
    path = tmp_path / "config.py"
    path.write_text(REQUIRED_BODY)

    config = load(path)
    prompts = load_prompts(config)

    assert isinstance(prompts.classify_relevance, SplitPromptTemplate)
    assert isinstance(prompts.judge_match, PromptTemplate)


# --- Prompts dataclass ---


def test_prompts_is_frozen() -> None:
    from application_pipeline.prompts import (
        JUDGE_TOP_N_SYSTEM_SLOTS,
        JUDGE_TOP_N_USER_SLOTS,
    )

    split = SplitPromptTemplate(
        system=PromptTemplate("system", frozenset()),
        user=PromptTemplate("{TITLE} {RAW_DESCRIPTION}", CLASSIFY_RELEVANCE_SLOTS),
    )
    prompts = Prompts(
        classify_relevance=split,
        judge_match=PromptTemplate("{skills} {raw_description}", JUDGE_MATCH_SLOTS),
        judge_top_n=SplitPromptTemplate(
            system=PromptTemplate("{skills}", JUDGE_TOP_N_SYSTEM_SLOTS),
            user=PromptTemplate("{candidates}", JUDGE_TOP_N_USER_SLOTS),
        ),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        prompts.classify_relevance = split  # type: ignore[misc]


# --- error hierarchy ---


def test_prompt_error_is_not_user_settings_error() -> None:
    from application_pipeline import UserSettingsError

    assert not issubclass(PromptError, UserSettingsError)


def test_prompt_error_is_exception() -> None:
    assert issubclass(PromptError, Exception)
