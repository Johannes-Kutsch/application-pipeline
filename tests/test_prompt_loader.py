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
from application_pipeline import triage_profile
from application_pipeline.prompts import (
    CLASSIFY_RELEVANCE_SLOTS,
    JUDGE_TOP_N_SLOTS,
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
    (triage / "candidate-profile.md").write_text("I am a developer\n")
    (triage / "gate-criteria.md").write_text("Hamburg, remote\n")
    (triage / "skills.md").write_text("- Python\n- SQL {always}\n")
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

    prompts = load_prompts(config)

    assert isinstance(prompts.classify_relevance, PromptTemplate)
    assert isinstance(prompts.judge_top_n, PromptTemplate)


def test_load_prompts_uses_triage_profile_module_for_profile_slot_loading(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config_with_user_info(tmp_path)

    def fake_load_prompt_slot_values(
        triage_profile_dir: pathlib.Path,
    ) -> dict[str, str]:
        assert triage_profile_dir == config.user_info_dir / "triage-profile"
        return {
            "CANDIDATE_PROFILE": "candidate from triage module",
            "GATE_CRITERIA": "gate from triage module",
            "SKILLS": "- skills from triage module",
        }

    monkeypatch.setattr(
        triage_profile, "load_prompt_slot_values", fake_load_prompt_slot_values
    )

    prompts = load_prompts(config)

    assert prompts.classify_relevance.render(
        LISTINGS="x"
    ) != prompts.classify_relevance.render(LISTINGS="x").replace(
        "gate from triage module", ""
    )
    assert prompts.judge_top_n.render(CANDIDATES="x") != prompts.judge_top_n.render(
        CANDIDATES="x"
    ).replace("candidate from triage module", "")
    assert prompts.judge_top_n.render(CANDIDATES="x") != prompts.judge_top_n.render(
        CANDIDATES="x"
    ).replace("- skills from triage module", "")


def test_load_prompts_classify_contains_gate_criteria_not_candidate_profile(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)
    rendered = prompts.classify_relevance.render(
        LISTINGS="## Stellenanzeige id=1\n\n- Jobtitel: x\n\ny"
    )

    assert "Hamburg, remote" in rendered
    assert "I am a developer" not in rendered


def test_load_prompts_classify_is_single_gate_check_no_skill_floor(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)
    rendered = prompts.classify_relevance.render(
        LISTINGS="## Stellenanzeige id=1\n\n- Jobtitel: x\n\ny"
    )

    assert "Skill" not in rendered
    assert "Erfahrungs" not in rendered


def test_load_prompts_judge_contains_candidate_profile_and_skills_not_gate_criteria(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)
    rendered = prompts.judge_top_n.render(CANDIDATES="x")

    assert "I am a developer" in rendered
    assert "- Python" in rendered
    assert "Hamburg, remote" not in rendered


def test_load_prompts_classify_contains_verdict_id_tag_instruction(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)

    prompts = load_prompts(config)
    rendered = prompts.classify_relevance.render(
        LISTINGS="## Stellenanzeige id=1\n\n- Jobtitel: x\n\ny"
    )

    assert '<verdict id="N">' in rendered


@pytest.mark.parametrize(
    "missing_file",
    ["candidate-profile.md", "gate-criteria.md"],
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
    ["candidate-profile.md", "gate-criteria.md"],
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
    (triage / "candidate-profile.md").write_text("background\n")
    (triage / "gate-criteria.md").write_text("Hamburg\n")
    path = tmp_path / "config.py"
    path.write_text(REQUIRED_BODY)

    config = load(path)
    prompts = load_prompts(config)

    assert isinstance(prompts.classify_relevance, PromptTemplate)
    assert isinstance(prompts.judge_top_n, PromptTemplate)


# --- Prompts dataclass ---


def test_prompts_is_frozen() -> None:
    tpl = PromptTemplate(
        "{LISTINGS}",
        CLASSIFY_RELEVANCE_SLOTS,
    )
    prompts = Prompts(
        classify_relevance=tpl,
        judge_top_n=PromptTemplate("{CANDIDATES}", JUDGE_TOP_N_SLOTS),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        prompts.classify_relevance = tpl  # type: ignore[misc]


# --- error hierarchy ---


def test_prompt_error_is_not_user_settings_error() -> None:
    from application_pipeline import UserSettingsError

    assert not issubclass(PromptError, UserSettingsError)


def test_prompt_error_is_exception() -> None:
    assert issubclass(PromptError, Exception)
