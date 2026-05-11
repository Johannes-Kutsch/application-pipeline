from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest

from application_pipeline import Config, Layout, load, load_layout
from application_pipeline.init_cmd import init
from application_pipeline.prompts import load_prompts


def _template_bytes(name: str) -> bytes:
    return (
        importlib.resources.files("application_pipeline.templates") / name
    ).read_bytes()


def _prompt_template_bytes(name: str) -> bytes:
    return (
        importlib.resources.files("application_pipeline.templates") / "prompts" / name
    ).read_bytes()


_PROMPT_FILES = (
    "classify_relevance.de.md",
    "classify_relevance.en.md",
    "judge_match.de.md",
    "judge_match.en.md",
)


def test_first_bootstrap_writes_both_files(tmp_path: Path) -> None:
    init(tmp_path)

    assert (tmp_path / "config.py").read_bytes() == _template_bytes("config.py")
    assert (tmp_path / "layout.py").read_bytes() == _template_bytes("layout.py")


def test_first_bootstrap_prints_wrote_for_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)

    out = capsys.readouterr().out
    assert "wrote config.py" in out
    assert "wrote layout.py" in out


def test_skip_existing_config_prints_correctly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config.py").write_text("# operator-edited\n")

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped config.py (already exists)" in out
    assert "wrote layout.py" in out


def test_both_exist_prints_skipped_for_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config.py").write_text("# custom\n")
    (tmp_path / "layout.py").write_text("# custom\n")

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped config.py (already exists)" in out
    assert "skipped layout.py (already exists)" in out


def test_skip_existing_config_preserves_content(tmp_path: Path) -> None:
    original = "# operator-edited content\nKEYWORDS = ['custom']\n"
    (tmp_path / "config.py").write_text(original)

    init(tmp_path)

    assert (tmp_path / "config.py").read_text() == original


def test_both_exist_neither_modified(tmp_path: Path) -> None:
    config_content = "# my config\n"
    layout_content = "# my layout\n"
    (tmp_path / "config.py").write_text(config_content)
    (tmp_path / "layout.py").write_text(layout_content)

    init(tmp_path)

    assert (tmp_path / "config.py").read_text() == config_content
    assert (tmp_path / "layout.py").read_text() == layout_content


def test_config_template_loads_successfully(tmp_path: Path) -> None:
    init(tmp_path)

    config = load(tmp_path / "config.py")

    assert isinstance(config, Config)
    assert config.keywords
    assert config.skills
    assert config.sources
    assert config.include_remote is True


def test_layout_template_loads_successfully(tmp_path: Path) -> None:
    init(tmp_path)

    layout = load_layout(tmp_path / "layout.py")

    assert isinstance(layout, Layout)
    assert set(layout.tier_emoji) == {"green", "amber", "red"}
    assert set(layout.tier_color) == {"green", "amber", "red"}


# --- New tests for prompt seeding ---


def test_fresh_seed_creates_full_tree(tmp_path: Path) -> None:
    init(tmp_path)

    assert (tmp_path / "config.py").exists()
    assert (tmp_path / "layout.py").exists()
    for fname in _PROMPT_FILES:
        dest = tmp_path / "prompts" / fname
        assert dest.exists(), f"expected {dest} to be seeded"
        assert dest.read_bytes() == _prompt_template_bytes(fname)


def test_fresh_seed_prints_all_six_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)

    out = capsys.readouterr().out
    assert "wrote config.py" in out
    assert "wrote layout.py" in out
    for fname in _PROMPT_FILES:
        assert f"wrote prompts/{fname}" in out


def test_seeded_prompts_load_without_error(tmp_path: Path) -> None:
    init(tmp_path)
    config = load(tmp_path / "config.py")

    prompts = load_prompts(config)

    assert set(prompts.classify_relevance) == {"de", "en"}
    assert set(prompts.judge_match) == {"de", "en"}


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    init(tmp_path)
    first_contents = {
        p: (tmp_path / p).read_bytes() for p in ["config.py", "layout.py"]
    } | {f"prompts/{f}": (tmp_path / "prompts" / f).read_bytes() for f in _PROMPT_FILES}

    init(tmp_path)

    for rel, original in first_contents.items():
        assert (tmp_path / rel).read_bytes() == original


def test_rerun_prints_all_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped config.py (already exists)" in out
    assert "skipped layout.py (already exists)" in out
    for fname in _PROMPT_FILES:
        assert f"skipped prompts/{fname} (already exists)" in out


def test_per_file_skip_leaves_existing_prompt_and_seeds_siblings(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompts").mkdir()
    existing = tmp_path / "prompts" / "classify_relevance.de.md"
    original_content = "# operator content\n"
    existing.write_text(original_content)

    init(tmp_path)

    assert existing.read_text() == original_content
    for fname in _PROMPT_FILES:
        if fname != "classify_relevance.de.md":
            assert (tmp_path / "prompts" / fname).exists(), f"{fname} should be seeded"


def test_per_file_skip_granular_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "classify_relevance.de.md").write_text("# custom\n")

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped prompts/classify_relevance.de.md (already exists)" in out
    for fname in _PROMPT_FILES:
        if fname != "classify_relevance.de.md":
            assert f"wrote prompts/{fname}" in out


def test_banner_does_not_trigger_prompt_error(tmp_path: Path) -> None:
    init(tmp_path)
    config = load(tmp_path / "config.py")

    # load_prompts parses every seeded file through string.Formatter().parse();
    # if any banner line uses {slot} syntax this raises PromptError
    load_prompts(config)
