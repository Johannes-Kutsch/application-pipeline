from __future__ import annotations

import importlib.resources
import pathlib

import pytest

from application_pipeline import Config, Layout, load, load_layout
from application_pipeline.init_cmd import init


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _template_bytes(name: str) -> bytes:
    return (
        importlib.resources.files("application_pipeline.templates") / name
    ).read_bytes()


def _make_prompts_dir(base: pathlib.Path) -> None:
    """Create a minimal prompts directory accepted by the Config Loader."""
    prompts = base / "prompts"
    prompts.mkdir(exist_ok=True)
    for fname in (
        "classify_relevance.de.md",
        "classify_relevance.en.md",
        "judge_match.de.md",
        "judge_match.en.md",
    ):
        (prompts / fname).write_text(f"{fname}\n")


# ---------------------------------------------------------------------------
# First-bootstrap: empty dir → both files written, bytes match templates
# ---------------------------------------------------------------------------


def test_first_bootstrap_writes_both_files(tmp_path: pathlib.Path) -> None:
    init(tmp_path)

    assert (tmp_path / "config.py").exists()
    assert (tmp_path / "layout.py").exists()
    assert (tmp_path / "config.py").read_bytes() == _template_bytes("config.py")
    assert (tmp_path / "layout.py").read_bytes() == _template_bytes("layout.py")


# ---------------------------------------------------------------------------
# Status-line output
# ---------------------------------------------------------------------------


def test_first_bootstrap_prints_wrote_for_both(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)

    out = capsys.readouterr().out
    assert "wrote config.py" in out
    assert "wrote layout.py" in out


def test_skip_existing_config_prints_correctly(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config.py").write_text("# operator-edited\n")

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped config.py (already exists)" in out
    assert "wrote layout.py" in out


def test_both_exist_prints_skipped_for_both(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config.py").write_text("# custom\n")
    (tmp_path / "layout.py").write_text("# custom\n")

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped config.py (already exists)" in out
    assert "skipped layout.py (already exists)" in out


# ---------------------------------------------------------------------------
# Skip-existing preserves content
# ---------------------------------------------------------------------------


def test_skip_existing_config_preserves_content(tmp_path: pathlib.Path) -> None:
    original = "# operator-edited content\nKEYWORDS = ['custom']\n"
    (tmp_path / "config.py").write_text(original)

    init(tmp_path)

    assert (tmp_path / "config.py").read_text() == original


def test_both_exist_neither_modified(tmp_path: pathlib.Path) -> None:
    config_content = "# my config\n"
    layout_content = "# my layout\n"
    (tmp_path / "config.py").write_text(config_content)
    (tmp_path / "layout.py").write_text(layout_content)

    init(tmp_path)

    assert (tmp_path / "config.py").read_text() == config_content
    assert (tmp_path / "layout.py").read_text() == layout_content


# ---------------------------------------------------------------------------
# Template roundtrip through loaders
# ---------------------------------------------------------------------------


def test_config_template_loads_successfully(tmp_path: pathlib.Path) -> None:
    init(tmp_path)
    _make_prompts_dir(tmp_path)

    config = load(tmp_path / "config.py")

    assert isinstance(config, Config)
    assert config.keywords
    assert config.skills
    assert config.sources
    assert config.include_remote is True


def test_layout_template_loads_successfully(tmp_path: pathlib.Path) -> None:
    init(tmp_path)

    layout = load_layout(tmp_path / "layout.py")

    assert isinstance(layout, Layout)
    assert set(layout.tier_emoji) == {"green", "amber", "red"}
    assert set(layout.tier_color) == {"green", "amber", "red"}
