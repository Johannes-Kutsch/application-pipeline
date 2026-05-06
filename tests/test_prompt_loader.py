import dataclasses
import pathlib
import textwrap

import pytest

from application_pipeline import (
    Config,
    ConfigError,
    Prompts,
    SourceEntry,
    load,
    load_prompts,
)


REQUIRED_BODY = textwrap.dedent(
    """
    from application_pipeline import SourceEntry

    KEYWORDS = ["python"]
    SKILLS = ["python"]
    SOURCES = [SourceEntry(parser_type="bundesagentur")]
    LOCATIONS = ["Hamburg"]
    """
)


def write_config(
    tmp_path: pathlib.Path,
    *,
    classify_text: str = "classify body\n",
    judge_text: str = "judge body\n",
) -> pathlib.Path:
    path = tmp_path / "config.py"
    path.write_text(REQUIRED_BODY)
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "classify_relevance.md").write_text(classify_text)
    (prompts / "judge_match.md").write_text(judge_text)
    return path


def test_load_prompts_returns_file_contents(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        classify_text="Classify this listing.\n",
        judge_text="Judge against skills.\n",
    )
    config = load(path)

    prompts = load_prompts(config)

    assert isinstance(prompts, Prompts)
    assert prompts.classify_relevance == "Classify this listing.\n"
    assert prompts.judge_match == "Judge against skills.\n"


def test_load_prompts_preserves_utf8(tmp_path: pathlib.Path) -> None:
    write_config(
        tmp_path,
        classify_text="Klassifiziere — Schlüssel: ✓\n",
        judge_text="Beurteile — Fähigkeiten: π\n",
    )
    config = load(tmp_path / "config.py")

    prompts = load_prompts(config)

    assert prompts.classify_relevance == "Klassifiziere — Schlüssel: ✓\n"
    assert prompts.judge_match == "Beurteile — Fähigkeiten: π\n"


def test_prompts_is_frozen() -> None:
    prompts = Prompts(classify_relevance="a", judge_match="b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        prompts.classify_relevance = "x"  # type: ignore[misc]


def test_load_prompts_raises_when_file_missing_after_config_load(
    tmp_path: pathlib.Path,
) -> None:
    write_config(tmp_path)
    config = load(tmp_path / "config.py")
    config.classify_relevance_prompt.unlink()

    with pytest.raises(ConfigError) as exc_info:
        load_prompts(config)
    message = str(exc_info.value)
    assert "CLASSIFY_RELEVANCE_PROMPT" in message
    assert str(config.classify_relevance_prompt) in message


def test_load_prompts_raises_when_file_becomes_empty(tmp_path: pathlib.Path) -> None:
    write_config(tmp_path)
    config = load(tmp_path / "config.py")
    config.judge_match_prompt.write_text("")

    with pytest.raises(ConfigError) as exc_info:
        load_prompts(config)
    message = str(exc_info.value)
    assert "JUDGE_MATCH_PROMPT" in message
    assert str(config.judge_match_prompt) in message


def test_load_prompts_accepts_config_constructed_directly(
    tmp_path: pathlib.Path,
) -> None:
    classify = tmp_path / "c.md"
    classify.write_text("c\n")
    judge = tmp_path / "j.md"
    judge.write_text("j\n")
    config = Config(
        keywords=["k"],
        skills=[],
        sources=[SourceEntry(parser_type="bundesagentur")],
        locations=["Hamburg"],
        classify_relevance_prompt=classify,
        judge_match_prompt=judge,
    )

    prompts = load_prompts(config)

    assert prompts.classify_relevance == "c\n"
    assert prompts.judge_match == "j\n"
