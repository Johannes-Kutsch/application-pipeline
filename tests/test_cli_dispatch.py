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
    assert "config.py" not in out


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
    (home / ".env").write_text("OPENCODE_GO_API_KEY=test-key\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    with pytest.raises(Exception, match="Missing required field: SOURCES"):
        _run_main(["run"])


def test_run_materialises_logs_in_settings_dir_runtime_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_dir = _prepare_run_settings_dir(tmp_path, monkeypatch)
    _stub_successful_run(monkeypatch)

    _run_main(["run"])

    assert (settings_dir / ".runtime-data" / "logs").is_dir()


def test_run_materialises_logs_in_settings_dir_not_cwd_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_dir = _prepare_run_settings_dir(tmp_path, monkeypatch)
    _stub_successful_run(monkeypatch)

    _run_main(["run"])

    assert (settings_dir / ".runtime-data" / "logs").is_dir()
    assert not (tmp_path / "logs").exists(), (
        "logs must not be created directly under cwd"
    )


def test_run_prints_completion_summary_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare_run_settings_dir(tmp_path, monkeypatch)
    _stub_successful_run(monkeypatch)

    _run_main(["run"])

    out = capsys.readouterr().out
    assert out.startswith("run complete:")


def test_run_no_judge_flag_exits_2_without_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """run --no-judge without config.py exits 2 with the normal precheck message."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        _run_main(["run", "--no-judge"])

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "no application-pipeline/config.py in" in stderr


def test_run_unknown_flag_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """run --unknown exits non-zero with usage."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(["run", "--unknown"])

    assert exc_info.value.code != 0
    stderr = capsys.readouterr().err
    assert "usage" in stderr


def test_project_scripts_entry_point_registered() -> None:
    """The installed package must expose an application-pipeline console script."""
    from importlib.metadata import entry_points

    scripts = {ep.name for ep in entry_points(group="console_scripts")}
    assert "application-pipeline" in scripts


# ---------------------------------------------------------------------------
# cron subcommand
# ---------------------------------------------------------------------------


def _make_config(home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    config_path = home / "config.py"
    config_path.write_text("# stub\n")
    (home / ".env").write_text("OPENCODE_GO_API_KEY=test-key\n", encoding="utf-8")
    return config_path


def _prepare_run_settings_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    settings_dir = tmp_path / "application-pipeline"
    _make_config(settings_dir)
    monkeypatch.chdir(tmp_path)
    return settings_dir


def _fake_run_summary() -> object:
    from dataclasses import dataclass

    @dataclass
    class Summary:
        discovered: int = 0
        skipped: int = 0
        prefilter_dropped: int = 0
        classifier_dropped: int = 0
        written: int = 0
        enrich_failed: int = 0
        errored: int = 0
        classify_items: int = 0
        claude_input_tokens: int = 0
        claude_output_tokens: int = 0
        claude_cache_read_tokens: int = 0
        claude_cost_usd: float = 0.0
        duration_seconds: float = 0.0

    return Summary()


def _stub_successful_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "application_pipeline.orchestrator.run",
        lambda *_a, **_kw: _fake_run_summary(),
    )


def test_cron_without_config_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        _run_main(["cron"])

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "no application-pipeline/config.py in" in stderr


def test_cron_unknown_flag_exits_nonzero_with_cron_in_usage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_main(["cron", "--unknown"])

    assert exc_info.value.code != 0
    stderr = capsys.readouterr().err
    assert "usage" in stderr
    assert "cron" in stderr


def test_cron_success_refreshes_workspace_and_prints_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "application-pipeline"
    _make_config(home)
    (home / "layout.py").write_text("# old layout\n")
    monkeypatch.chdir(tmp_path)
    _stub_successful_run(monkeypatch)

    _run_main(["cron"])

    assert not (home / "layout.py").exists()
    assert (home / ".runtime-data" / "logs").is_dir()
    out = capsys.readouterr().out
    assert "removed layout.py" in out
    assert "run complete:" in out


def test_cron_no_judge_accepts_flag_and_runs_successfully(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "application-pipeline"
    _make_config(home)
    monkeypatch.chdir(tmp_path)
    _stub_successful_run(monkeypatch)

    _run_main(["cron", "--no-judge"])

    assert (home / ".runtime-data" / "logs").is_dir()
    assert "run complete:" in capsys.readouterr().out
