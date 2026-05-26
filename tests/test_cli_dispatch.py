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
    return config_path


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


def test_cron_success_runs_init_refresh_then_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "application-pipeline"
    _make_config(home)
    monkeypatch.chdir(tmp_path)

    calls: list[str] = []

    def fake_init(cwd: Path, *, refresh: bool) -> None:
        calls.append(f"init refresh={refresh}")

    def fake_run(
        config_path: Path, *, status_display: object, run_log: object, no_judge: bool
    ) -> object:
        calls.append(f"run no_judge={no_judge}")
        return _fake_run_summary()

    monkeypatch.setattr("application_pipeline.init_cmd.init", fake_init)
    monkeypatch.setattr("application_pipeline.orchestrator.run", fake_run)

    _run_main(["cron"])

    assert calls == ["init refresh=True", "run no_judge=False"]


def test_cron_no_judge_passes_through(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "application-pipeline"
    _make_config(home)
    monkeypatch.chdir(tmp_path)

    received: dict[str, bool] = {}

    def fake_init(cwd: Path, *, refresh: bool) -> None:
        pass

    def fake_run(
        config_path: Path, *, status_display: object, run_log: object, no_judge: bool
    ) -> object:
        received["no_judge"] = no_judge
        return _fake_run_summary()

    monkeypatch.setattr("application_pipeline.init_cmd.init", fake_init)
    monkeypatch.setattr("application_pipeline.orchestrator.run", fake_run)

    _run_main(["cron", "--no-judge"])

    assert received["no_judge"] is True


def test_cron_init_failure_writes_failure_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "application-pipeline"
    _make_config(home)
    monkeypatch.chdir(tmp_path)

    def fake_init(cwd: Path, *, refresh: bool) -> None:
        raise RuntimeError("init blew up")

    monkeypatch.setattr("application_pipeline.init_cmd.init", fake_init)

    with pytest.raises(SystemExit) as exc_info:
        _run_main(["cron"])

    assert exc_info.value.code != 0
    failures_dir = home / ".runtime-data" / "failures"
    reports = list(failures_dir.glob("*.md"))
    assert len(reports) == 1
    content = reports[0].read_text()
    assert "init --refresh" in content


def test_cron_run_failure_writes_failure_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "application-pipeline"
    _make_config(home)
    monkeypatch.chdir(tmp_path)

    def fake_init(cwd: Path, *, refresh: bool) -> None:
        pass

    def fake_run(
        config_path: Path, *, status_display: object, run_log: object, no_judge: bool
    ) -> object:
        raise RuntimeError("run blew up")

    monkeypatch.setattr("application_pipeline.init_cmd.init", fake_init)
    monkeypatch.setattr("application_pipeline.orchestrator.run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        _run_main(["cron"])

    assert exc_info.value.code != 0
    failures_dir = home / ".runtime-data" / "failures"
    reports = list(failures_dir.glob("*.md"))
    assert len(reports) == 1
