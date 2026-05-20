"""Tests for the CLI dispatch layer in __main__.main()."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _run_main(args: list[str]) -> None:
    """Call main() with sys.argv set to [prog, *args]."""
    from application_pipeline.__main__ import main

    old = sys.argv
    try:
        sys.argv = ["application-pipeline", *args]
        main()
    finally:
        sys.argv = old


# ---------------------------------------------------------------------------
# init --refresh flag
# ---------------------------------------------------------------------------


def test_init_refresh_seeds_files_on_fresh_dir(tmp_path: Path) -> None:
    _run_main(["init", "--refresh", str(tmp_path)])

    assert (tmp_path / "config.py").exists()
    assert (tmp_path / "layout.py").exists()


def test_init_refresh_skips_existing_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config.py").write_text("# custom\n")

    _run_main(["init", "--refresh", str(tmp_path)])

    out = capsys.readouterr().out
    assert "skipped config.py (already exists)" in out


def test_init_refresh_is_idempotent(tmp_path: Path) -> None:
    _run_main(["init", "--refresh", str(tmp_path)])
    first = (tmp_path / "config.py").read_bytes()

    _run_main(["init", "--refresh", str(tmp_path)])

    assert (tmp_path / "config.py").read_bytes() == first


# ---------------------------------------------------------------------------
# run subcommand
# ---------------------------------------------------------------------------


def test_run_subcommand_exits_nonzero_on_bad_config(tmp_path: Path) -> None:
    bad_config = tmp_path / "config.py"
    bad_config.write_text("KEYWORDS = ['python']\n")  # missing SOURCES

    with pytest.raises(SystemExit) as exc_info:
        _run_main(["run", str(bad_config)])

    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# console-script registration
# ---------------------------------------------------------------------------


def test_project_scripts_entry_point_registered() -> None:
    """The installed package must expose an application-pipeline console script."""
    from importlib.metadata import entry_points

    scripts = {ep.name for ep in entry_points(group="console_scripts")}
    assert "application-pipeline" in scripts
