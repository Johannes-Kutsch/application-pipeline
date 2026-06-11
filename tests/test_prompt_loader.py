import dataclasses
import importlib.resources
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


def test_load_prompts_judge_allows_missing_optional_skills_file(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)
    (config.user_info_dir / "triage-profile" / "skills.md").unlink()

    prompts = load_prompts(config)
    rendered = prompts.judge_top_n.render(CANDIDATES="x")

    assert "I am a developer" in rendered
    assert "Hamburg, remote" not in rendered
    assert "- Python" not in rendered


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
    ("call_site", "template_text", "expected_slot"),
    [
        (
            "classify_relevance",
            "{CANDIDATE_PROFILE}\n{LISTINGS}\n",
            "CANDIDATE_PROFILE",
        ),
        ("classify_relevance", "{SKILLS}\n{LISTINGS}\n", "SKILLS"),
        ("judge_top_n", "{GATE_CRITERIA}\n{CANDIDATES}\n", "GATE_CRITERIA"),
    ],
)
def test_load_prompts_rejects_triage_profile_slots_routed_to_wrong_call_site(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    call_site: str,
    template_text: str,
    expected_slot: str,
) -> None:
    config = make_config_with_user_info(tmp_path)
    prompt_pkg = tmp_path / "prompt-pkg"
    prompt_pkg.mkdir()
    (prompt_pkg / "classify_relevance.md").write_text("{GATE_CRITERIA}\n{LISTINGS}\n")
    (prompt_pkg / "judge_top_n.md").write_text(
        "{CANDIDATE_PROFILE}\n{SKILLS}\n{CANDIDATES}\n"
    )
    (prompt_pkg / f"{call_site}.md").write_text(template_text)

    monkeypatch.setattr(importlib.resources, "files", lambda _: prompt_pkg)

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config)

    assert expected_slot in str(exc_info.value)


def test_load_prompts_renders_triage_profile_braces_literally(
    tmp_path: pathlib.Path,
) -> None:
    config = make_config_with_user_info(tmp_path)
    triage_dir = config.user_info_dir / "triage-profile"
    (triage_dir / "gate-criteria.md").write_text("Gate {python} {{remote}} }\n")
    (triage_dir / "candidate-profile.md").write_text("Candidate {ml} {{ranking}} }\n")
    (triage_dir / "skills.md").write_text("- Skill {sql} text\n- Skill {{etl}} text\n")

    prompts = load_prompts(config)
    classify_rendered = prompts.classify_relevance.render(LISTINGS="listing")
    judge_rendered = prompts.judge_top_n.render(CANDIDATES="candidate")

    assert "Gate {python} {{remote}} }" in classify_rendered
    assert "Candidate {ml} {{ranking}} }" in judge_rendered
    assert "- Skill {sql} text\n- Skill {{etl}} text" in judge_rendered


@pytest.mark.parametrize(
    ("call_site", "template_text", "expected_text"),
    [
        ("classify_relevance", "No listing slot here\n", "missing required data slots"),
        ("judge_top_n", "{CANDIDATES}\n{EXTRA}\n", "unknown slots"),
    ],
)
def test_load_prompts_surfaces_template_slot_validation_as_prompt_error(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    call_site: str,
    template_text: str,
    expected_text: str,
) -> None:
    config = make_config_with_user_info(tmp_path)
    prompt_pkg = tmp_path / "prompt-pkg"
    prompt_pkg.mkdir()
    (prompt_pkg / "classify_relevance.md").write_text("{GATE_CRITERIA}\n{LISTINGS}\n")
    (prompt_pkg / "judge_top_n.md").write_text(
        "{CANDIDATE_PROFILE}\n{SKILLS}\n{CANDIDATES}\n"
    )
    (prompt_pkg / f"{call_site}.md").write_text(template_text)

    monkeypatch.setattr(importlib.resources, "files", lambda _: prompt_pkg)

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config)

    assert expected_text in str(exc_info.value)


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


@pytest.mark.parametrize(
    ("legacy_filename", "expected_text"),
    [
        ("domain-fit.md", "gate-criteria.md"),
        ("self-description.md", "candidate-profile.md"),
        ("match-criteria.md", "gate-criteria.md"),
    ],
)
def test_load_prompts_raises_for_legacy_triage_profile_filename(
    tmp_path: pathlib.Path, legacy_filename: str, expected_text: str
) -> None:
    config = make_config_with_user_info(tmp_path)
    (config.user_info_dir / "triage-profile" / legacy_filename).write_text("legacy\n")

    with pytest.raises(PromptError) as exc_info:
        load_prompts(config)

    assert legacy_filename in str(exc_info.value)
    assert expected_text in str(exc_info.value)


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
