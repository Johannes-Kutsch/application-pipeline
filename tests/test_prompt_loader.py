import dataclasses
import pathlib

import pytest

from application_pipeline import (
    Config,
    Prompts,
    PromptError,
    SourceEntry,
    load,
    load_prompts,
)


REQUIRED_BODY = """
from application_pipeline import SourceEntry

KEYWORDS = ["python"]
SKILLS = ["python"]
SOURCES = [SourceEntry(parser_type="bundesagentur")]
LOCATIONS = ["Hamburg"]
"""


def write_prompts(
    prompts_dir: pathlib.Path,
    *,
    classify_de: str = "classify de\n",
    classify_en: str = "classify en\n",
    judge_de: str = "judge de\n",
    judge_en: str = "judge en\n",
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


def test_load_prompts_returns_per_language_dicts(tmp_path: pathlib.Path) -> None:
    write_prompts(
        tmp_path / "prompts",
        classify_de="Klassifiziere.\n",
        classify_en="Classify.\n",
        judge_de="Beurteile.\n",
        judge_en="Judge.\n",
    )
    config = make_config(tmp_path)

    prompts = load_prompts(config)

    assert isinstance(prompts, Prompts)
    assert prompts.classify_relevance["de"] == "Klassifiziere.\n"
    assert prompts.classify_relevance["en"] == "Classify.\n"
    assert prompts.judge_match["de"] == "Beurteile.\n"
    assert prompts.judge_match["en"] == "Judge.\n"


def test_load_prompts_preserves_utf8(tmp_path: pathlib.Path) -> None:
    write_prompts(
        tmp_path / "prompts",
        classify_de="Klassifiziere — Schlüssel: ✓\n",
        classify_en="Classify — key: ✓\n",
        judge_de="Beurteile — Fähigkeiten: π\n",
        judge_en="Judge — skills: π\n",
    )
    config = make_config(tmp_path)

    prompts = load_prompts(config)

    assert prompts.classify_relevance["de"] == "Klassifiziere — Schlüssel: ✓\n"
    assert prompts.classify_relevance["en"] == "Classify — key: ✓\n"
    assert prompts.judge_match["de"] == "Beurteile — Fähigkeiten: π\n"
    assert prompts.judge_match["en"] == "Judge — skills: π\n"


def test_prompts_is_frozen(tmp_path: pathlib.Path) -> None:
    write_prompts(tmp_path / "prompts")
    config = make_config(tmp_path)
    prompts = load_prompts(config)
    with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
        prompts.classify_relevance = {}  # type: ignore[misc]


def test_load_prompts_via_load(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "config.py"
    path.write_text(REQUIRED_BODY)
    write_prompts(tmp_path / "prompts")

    config = load(path)
    prompts = load_prompts(config)

    assert "de" in prompts.classify_relevance
    assert "en" in prompts.classify_relevance


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
