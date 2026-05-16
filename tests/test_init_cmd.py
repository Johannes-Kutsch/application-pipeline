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


def _user_info_template_bytes(name: str) -> bytes:
    return (
        importlib.resources.files("application_pipeline.templates") / "user-info" / name
    ).read_bytes()


_USER_INFO_FILES = (
    "self-description.md",
    "domain-fit.md",
    "match-criteria.md",
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


# --- User-info file seeding ---


def test_fresh_seed_creates_user_info_tree(tmp_path: Path) -> None:
    init(tmp_path)

    assert (tmp_path / "config.py").exists()
    assert (tmp_path / "layout.py").exists()
    for fname in _USER_INFO_FILES:
        dest = tmp_path / "user-info" / fname
        assert dest.exists(), f"expected {dest} to be seeded"
        assert dest.read_bytes() == _user_info_template_bytes(fname)


def test_fresh_seed_does_not_create_prompts_dir(tmp_path: Path) -> None:
    init(tmp_path)

    assert not (tmp_path / "prompts").exists()


def test_fresh_seed_prints_all_five_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)

    out = capsys.readouterr().out
    assert "wrote config.py" in out
    assert "wrote layout.py" in out
    for fname in _USER_INFO_FILES:
        assert f"wrote user-info/{fname}" in out


def test_seeded_config_and_user_info_load_prompts_without_error(tmp_path: Path) -> None:
    init(tmp_path)
    config = load(tmp_path / "config.py")

    prompts = load_prompts(config)

    assert set(prompts.classify_relevance) == {"de", "en"}
    assert set(prompts.judge_match) == {"de", "en"}


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    init(tmp_path)
    first_contents = {
        p: (tmp_path / p).read_bytes() for p in ["config.py", "layout.py"]
    } | {
        f"user-info/{f}": (tmp_path / "user-info" / f).read_bytes()
        for f in _USER_INFO_FILES
    }

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
    for fname in _USER_INFO_FILES:
        assert f"skipped user-info/{fname} (already exists)" in out


def test_per_file_skip_leaves_existing_user_info_and_seeds_siblings(
    tmp_path: Path,
) -> None:
    (tmp_path / "user-info").mkdir()
    existing = tmp_path / "user-info" / "self-description.md"
    original_content = "# operator content\n"
    existing.write_text(original_content)

    init(tmp_path)

    assert existing.read_text() == original_content
    for fname in _USER_INFO_FILES:
        if fname != "self-description.md":
            assert (tmp_path / "user-info" / fname).exists(), (
                f"{fname} should be seeded"
            )


def test_per_file_skip_granular_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "user-info").mkdir()
    (tmp_path / "user-info" / "self-description.md").write_text("# custom\n")

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped user-info/self-description.md (already exists)" in out
    for fname in _USER_INFO_FILES:
        if fname != "self-description.md":
            assert f"wrote user-info/{fname}" in out


def test_banner_does_not_trigger_prompt_error(tmp_path: Path) -> None:
    init(tmp_path)
    config = load(tmp_path / "config.py")

    # load_prompts injects user-info content into package templates;
    # if any user-info template line uses raw {slot} syntax this would raise PromptError
    load_prompts(config)
