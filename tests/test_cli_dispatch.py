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


def test_init_no_arg_seeds_at_cwd_application_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _run_main(["init"])
    assert (tmp_path / "application-pipeline" / "config.py").exists()
    assert not (tmp_path / "application-pipeline" / "layout.py").exists()


def test_init_refresh_seeds_files_on_fresh_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _run_main(["init", "--refresh"])

    assert (tmp_path / "application-pipeline" / "config.py").exists()
    assert not (tmp_path / "application-pipeline" / "layout.py").exists()


def test_init_refresh_removes_layout_py_if_present(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "application-pipeline"
    home.mkdir()
    (home / "layout.py").write_text("# old layout\n")

    monkeypatch.chdir(tmp_path)
    _run_main(["init", "--refresh"])

    assert not (home / "layout.py").exists()
    out = capsys.readouterr().out
    assert "removed layout.py" in out


def test_init_refresh_preserves_user_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "application-pipeline"
    home.mkdir()
    (home / "config.py").write_text("# custom\n")

    monkeypatch.chdir(tmp_path)
    _run_main(["init", "--refresh"])

    out = capsys.readouterr().out
    assert "skipped config.py (preserved)" in out


def test_init_refresh_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _run_main(["init", "--refresh"])
    first = (tmp_path / "application-pipeline" / "config.py").read_bytes()

    _run_main(["init", "--refresh"])

    assert (tmp_path / "application-pipeline" / "config.py").read_bytes() == first


def test_init_legacy_positional_arg_exits_nonzero(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_main(["init", str(tmp_path)])

    assert exc_info.value.code != 0


def test_run_without_config_dir_exits_2_with_precheck_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)  # no application-pipeline/ subfolder

    with pytest.raises(SystemExit) as exc_info:
        _run_main(["run"])

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "no application-pipeline/config.py in" in stderr
    assert "did you forget to cd, or run init?" in stderr


def test_run_with_positional_arg_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_main(["run", str(tmp_path / "config.py")])

    assert exc_info.value.code != 0
    stderr = capsys.readouterr().err
    assert "usage" in stderr


def test_legacy_implicit_run_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_main([str(tmp_path / "config.py")])

    assert exc_info.value.code != 0
    stderr = capsys.readouterr().err
    assert "usage" in stderr


def test_run_exits_nonzero_on_bad_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "application-pipeline"
    home.mkdir()
    (home / "config.py").write_text("KEYWORDS = ['python']\n")  # missing SOURCES

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        _run_main(["run"])

    assert exc_info.value.code != 0


def test_project_scripts_entry_point_registered() -> None:
    """The installed package must expose an application-pipeline console script."""
    from importlib.metadata import entry_points

    scripts = {ep.name for ep in entry_points(group="console_scripts")}
    assert "application-pipeline" in scripts
