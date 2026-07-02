from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from application_pipeline.run_metrics import RunSummary
from application_pipeline.startup_runner import (
    StartupRequest,
    render_completion_summary,
    run_startup,
)
from application_pipeline.status_display import PlainStatusDisplay, RichStatusDisplay


def _write_settings_dir(
    cwd: Path, *, with_operator_credential: bool = True, with_layout: bool = False
) -> Path:
    settings_dir = cwd / "application-pipeline"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "config.py").write_text("", encoding="utf-8")
    if with_operator_credential:
        (settings_dir / ".env").write_text(
            "OPENCODE_GO_API_KEY=test-key\n", encoding="utf-8"
        )
    if with_layout:
        (settings_dir / "layout.py").write_text("# retired layout\n", encoding="utf-8")
    return settings_dir


def _fake_summary() -> RunSummary:
    return RunSummary(
        discovered=3,
        skipped=1,
        prefilter_dropped=1,
        classifier_dropped=1,
        written=2,
        enrich_failed=0,
        errored=0,
        classify_items=3,
        duration_seconds=1.5,
    )


def test_startup_request_carries_startup_runner_inputs(tmp_path: Path) -> None:
    request = StartupRequest(
        cwd=tmp_path,
        mode="cron",
        no_judge=True,
        has_terminal=False,
    )

    assert request == StartupRequest(
        cwd=tmp_path,
        mode="cron",
        no_judge=True,
        has_terminal=False,
    )


def test_render_completion_summary_matches_current_cli_fields() -> None:
    summary = RunSummary(
        discovered=3,
        skipped=1,
        prefilter_dropped=2,
        classifier_dropped=4,
        written=5,
        enrich_failed=6,
        errored=7,
        classify_items=8,
        duration_seconds=1.5,
    )

    rendered = render_completion_summary(summary)

    assert rendered == (
        "run complete:"
        "  discovered=3"
        "  skipped=1"
        "  prefilter_dropped=2"
        "  classifier_dropped=4"
        "  written=5"
        "  enrich_failed=6"
        "  errored=7"
        "  classify_items=8"
        "  duration=1.5s"
    )


def test_run_startup_prints_completion_summary_from_orchestrator_run_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_settings_dir(tmp_path, with_operator_credential=True)
    summary = RunSummary(
        discovered=11,
        skipped=12,
        prefilter_dropped=13,
        classifier_dropped=14,
        written=15,
        enrich_failed=16,
        errored=17,
        classify_items=18,
        duration_seconds=19.25,
    )
    monkeypatch.setattr(
        "application_pipeline.orchestrator.run",
        lambda *_a, **_kw: summary,
    )
    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        lambda *_a, **_kw: None,
    )

    run_startup(
        StartupRequest(
            cwd=tmp_path,
            mode="run",
            no_judge=False,
            has_terminal=False,
        )
    )

    assert capsys.readouterr().out == (
        "run complete:"
        "  discovered=11"
        "  skipped=12"
        "  prefilter_dropped=13"
        "  classifier_dropped=14"
        "  written=15"
        "  enrich_failed=16"
        "  errored=17"
        "  classify_items=18"
        "  duration=19.2s\n"
    )


def test_run_startup_runs_maintenance_after_emitting_summary_with_settings_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_dir = _write_settings_dir(tmp_path, with_operator_credential=True)
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        "application_pipeline.orchestrator.run",
        lambda *_a, **_kw: _fake_summary(),
    )

    def fake_maintenance(logs_dir: Path, failures_dir: Path) -> None:
        observed["logs_dir"] = logs_dir
        observed["failures_dir"] = failures_dir
        observed["stdout_before_maintenance"] = capsys.readouterr().out

    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        fake_maintenance,
    )

    run_startup(
        StartupRequest(
            cwd=tmp_path,
            mode="run",
            no_judge=False,
            has_terminal=False,
        )
    )

    assert observed == {
        "logs_dir": settings_dir / ".runtime-data" / "logs",
        "failures_dir": settings_dir / ".runtime-data" / "failures",
        "stdout_before_maintenance": (
            "run complete:"
            "  discovered=3"
            "  skipped=1"
            "  prefilter_dropped=1"
            "  classifier_dropped=1"
            "  written=2"
            "  enrich_failed=0"
            "  errored=0"
            "  classify_items=3"
            "  duration=1.5s\n"
        ),
    }


def test_run_startup_skips_maintenance_when_orchestrator_does_not_return_run_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_settings_dir(tmp_path, with_operator_credential=True)
    maintenance_called = False

    class SummaryLike:
        discovered = 3
        skipped = 1
        prefilter_dropped = 1
        classifier_dropped = 1
        written = 2
        enrich_failed = 0
        errored = 0
        classify_items = 3
        duration_seconds = 1.5

    monkeypatch.setattr(
        "application_pipeline.orchestrator.run",
        lambda *_a, **_kw: SummaryLike(),
    )

    def fake_maintenance(*_args: object, **_kwargs: object) -> None:
        nonlocal maintenance_called
        maintenance_called = True

    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        fake_maintenance,
    )

    run_startup(
        StartupRequest(
            cwd=tmp_path,
            mode="run",
            no_judge=False,
            has_terminal=False,
        )
    )

    assert maintenance_called is False
    assert capsys.readouterr().out == (
        "run complete:"
        "  discovered=3"
        "  skipped=1"
        "  prefilter_dropped=1"
        "  classifier_dropped=1"
        "  written=2"
        "  enrich_failed=0"
        "  errored=0"
        "  classify_items=3"
        "  duration=1.5s\n"
    )


def test_run_startup_suppresses_maintenance_exception_after_successful_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_settings_dir(tmp_path, with_operator_credential=True)
    monkeypatch.setattr(
        "application_pipeline.orchestrator.run",
        lambda *_a, **_kw: _fake_summary(),
    )
    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("maintenance exploded")),
    )

    run_startup(
        StartupRequest(
            cwd=tmp_path,
            mode="run",
            no_judge=False,
            has_terminal=False,
        )
    )

    assert capsys.readouterr().out == (
        "run complete:"
        "  discovered=3"
        "  skipped=1"
        "  prefilter_dropped=1"
        "  classifier_dropped=1"
        "  written=2"
        "  enrich_failed=0"
        "  errored=0"
        "  classify_items=3"
        "  duration=1.5s\n"
    )


def test_run_startup_skips_maintenance_when_orchestrator_run_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_settings_dir(tmp_path, with_operator_credential=True)
    maintenance_called = False

    def fake_maintenance(*_args: object, **_kwargs: object) -> None:
        nonlocal maintenance_called
        maintenance_called = True

    monkeypatch.setattr(
        "application_pipeline.orchestrator.run",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("run failed")),
    )
    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        fake_maintenance,
    )

    with pytest.raises(RuntimeError, match="run failed"):
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="run",
                no_judge=False,
                has_terminal=False,
            )
        )

    assert maintenance_called is False
    assert capsys.readouterr().out == ""


def test_startup_runner_without_config_exits_2_with_guidance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request = StartupRequest(
        cwd=tmp_path,
        mode="run",
        no_judge=False,
        has_terminal=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        run_startup(request)

    assert exc_info.value.code == 2
    assert "no application-pipeline/config.py in" in capsys.readouterr().err


def test_cron_startup_without_config_exits_2_with_guidance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request = StartupRequest(
        cwd=tmp_path,
        mode="cron",
        no_judge=False,
        has_terminal=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        run_startup(request)

    assert exc_info.value.code == 2
    assert "no application-pipeline/config.py in" in capsys.readouterr().err


def test_startup_runner_inside_settings_dir_emits_cd_up_guidance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir(parents=True)
    (settings_dir / "config.py").write_text("", encoding="utf-8")

    request = StartupRequest(
        cwd=settings_dir,
        mode="run",
        no_judge=False,
        has_terminal=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        run_startup(request)

    assert exc_info.value.code == 2
    assert "cd .." in capsys.readouterr().err


def test_missing_config_startup_does_not_create_log_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="run",
                no_judge=False,
                has_terminal=False,
            )
        )

    assert not (tmp_path / ".runtime-data").exists()
    assert not (tmp_path / "application-pipeline" / ".runtime-data").exists()


def test_cron_missing_config_does_not_invoke_init_bootstrap(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit):
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="cron",
                no_judge=False,
                has_terminal=False,
            )
        )

    # Init Bootstrap materialises files under application-pipeline/ when invoked;
    # the directory must not exist after a missing-config exit.
    assert not (tmp_path / "application-pipeline").exists()


def test_credential_failure_does_not_create_log_artifacts(
    tmp_path: Path,
) -> None:
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir(parents=True)
    (settings_dir / "config.py").write_text("", encoding="utf-8")
    (settings_dir / ".env").write_text("OPENCODE_GO_API_KEY=\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="run",
                no_judge=False,
                has_terminal=False,
            )
        )

    assert not (settings_dir / ".runtime-data").exists()


def test_cron_credential_failure_happens_before_init_bootstrap(
    tmp_path: Path,
) -> None:
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir(parents=True)
    (settings_dir / "config.py").write_text("", encoding="utf-8")
    # No .env — credential check must fail before init runs

    with pytest.raises(SystemExit):
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="cron",
                no_judge=False,
                has_terminal=False,
            )
        )

    # Init Bootstrap materialises files when invoked; layout.py would be
    # removed and seeded files would appear if init ran. Verify it did not.
    assert not (settings_dir / "setup").exists()


def test_run_startup_exits_2_with_operator_credential_message_when_key_absent_from_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir(parents=True)
    (settings_dir / "config.py").write_text("", encoding="utf-8")
    (settings_dir / ".env").write_text("OTHER_VAR=some-value\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="run",
                no_judge=False,
                has_terminal=False,
            )
        )

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "startup failed" in stderr
    assert "operator credential" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_cron_startup_exits_2_with_operator_credential_message_when_key_absent_from_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir(parents=True)
    (settings_dir / "config.py").write_text("", encoding="utf-8")
    (settings_dir / ".env").write_text("OTHER_VAR=some-value\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="cron",
                no_judge=False,
                has_terminal=False,
            )
        )

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "startup failed" in stderr
    assert "operator credential" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_run_startup_exits_2_with_operator_credential_message_when_key_is_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir(parents=True)
    (settings_dir / "config.py").write_text("", encoding="utf-8")
    (settings_dir / ".env").write_text("OPENCODE_GO_API_KEY=\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="run",
                no_judge=False,
                has_terminal=False,
            )
        )

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "startup failed" in stderr
    assert "operator credential" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_cron_startup_exits_2_with_operator_credential_message_when_key_is_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir(parents=True)
    (settings_dir / "config.py").write_text("", encoding="utf-8")
    (settings_dir / ".env").write_text("OPENCODE_GO_API_KEY=\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="cron",
                no_judge=False,
                has_terminal=False,
            )
        )

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "startup failed" in stderr
    assert "operator credential" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_cron_startup_exits_2_with_operator_credential_message_when_env_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_settings_dir(tmp_path, with_operator_credential=False)

    with pytest.raises(SystemExit) as exc_info:
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="cron",
                no_judge=False,
                has_terminal=False,
            )
        )

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "startup failed" in stderr
    assert "operator credential" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_startup_runner_requires_operator_credential_before_parser_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_dir = _write_settings_dir(tmp_path, with_operator_credential=False)

    with pytest.raises(SystemExit) as exc_info:
        run_startup(
            StartupRequest(
                cwd=tmp_path,
                mode="run",
                no_judge=False,
                has_terminal=False,
            )
        )

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "startup failed" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr
    assert not (settings_dir / ".runtime-data" / "logs").exists()


def test_run_startup_passes_config_no_judge_and_runtime_logs_to_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_dir = _write_settings_dir(tmp_path, with_operator_credential=True)
    captured: dict[str, object] = {}

    def fake_run(config_path: Path, **kwargs: object) -> RunSummary:
        captured["config_path"] = config_path
        captured["no_judge"] = kwargs["no_judge"]
        run_log = kwargs["run_log"]
        assert hasattr(run_log, "event")
        run_log.event("pipeline_startup", "forwarded")
        return _fake_summary()

    monkeypatch.setattr("application_pipeline.orchestrator.run", fake_run)
    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        lambda *_a, **_kw: None,
    )

    run_startup(
        StartupRequest(
            cwd=tmp_path,
            mode="run",
            no_judge=True,
            has_terminal=False,
        )
    )

    assert captured == {
        "config_path": settings_dir / "config.py",
        "no_judge": True,
    }
    assert (
        settings_dir / ".runtime-data" / "logs" / "pipeline" / "startup.events.jsonl"
    ).exists()
    assert not (tmp_path / ".runtime-data" / "logs").exists()


def test_cron_startup_runner_refreshes_workspace_before_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_dir = _write_settings_dir(
        tmp_path, with_operator_credential=True, with_layout=True
    )
    monkeypatch.setattr(
        "application_pipeline.orchestrator.run",
        lambda *_a, **_kw: _fake_summary(),
    )
    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        lambda *_a, **_kw: None,
    )

    run_startup(
        StartupRequest(
            cwd=tmp_path,
            mode="cron",
            no_judge=False,
            has_terminal=False,
        )
    )

    assert not (settings_dir / "layout.py").exists()
    out = capsys.readouterr().out
    assert "removed layout.py" in out
    assert "run complete:" in out


def test_run_startup_runner_does_not_refresh_workspace_before_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_dir = _write_settings_dir(
        tmp_path, with_operator_credential=True, with_layout=True
    )
    monkeypatch.setattr(
        "application_pipeline.orchestrator.run",
        lambda *_a, **_kw: _fake_summary(),
    )
    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        lambda *_a, **_kw: None,
    )

    run_startup(
        StartupRequest(
            cwd=tmp_path,
            mode="run",
            no_judge=False,
            has_terminal=False,
        )
    )

    assert (settings_dir / "layout.py").exists()


def test_cron_startup_runner_keeps_success_path_behavior_after_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_dir = _write_settings_dir(tmp_path, with_operator_credential=True)
    events: list[tuple[object, ...]] = []

    def fake_init(cwd: Path, *, refresh: bool) -> None:
        events.append(("init", cwd, refresh))

    def fake_run(config_path: Path, **kwargs: object) -> RunSummary:
        events.append(("run", config_path, kwargs["no_judge"]))
        return _fake_summary()

    def fake_maintenance(logs_dir: Path, failures_dir: Path) -> None:
        events.append(("maintenance", logs_dir, failures_dir))
        stdout_before_maintenance = capsys.readouterr().out
        assert stdout_before_maintenance == (
            "run complete:"
            "  discovered=3"
            "  skipped=1"
            "  prefilter_dropped=1"
            "  classifier_dropped=1"
            "  written=2"
            "  enrich_failed=0"
            "  errored=0"
            "  classify_items=3"
            "  duration=1.5s\n"
        )

    monkeypatch.setattr("application_pipeline.init_cmd.init", fake_init)
    monkeypatch.setattr("application_pipeline.orchestrator.run", fake_run)
    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        fake_maintenance,
    )

    run_startup(
        StartupRequest(
            cwd=tmp_path,
            mode="cron",
            no_judge=True,
            has_terminal=False,
        )
    )

    assert events == [
        ("init", tmp_path, True),
        ("run", settings_dir / "config.py", True),
        (
            "maintenance",
            settings_dir / ".runtime-data" / "logs",
            settings_dir / ".runtime-data" / "failures",
        ),
    ]


def test_startup_runner_uses_plain_status_display_without_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_settings_dir(tmp_path, with_operator_credential=True)
    captured_display: list[object] = []

    def fake_run(*_args: object, **kwargs: object) -> RunSummary:
        captured_display.append(kwargs["status_display"])
        return _fake_summary()

    monkeypatch.setattr("application_pipeline.orchestrator.run", fake_run)
    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        lambda *_a, **_kw: None,
    )

    run_startup(
        StartupRequest(
            cwd=tmp_path,
            mode="run",
            no_judge=False,
            has_terminal=False,
        )
    )

    assert isinstance(captured_display[0], PlainStatusDisplay)


def test_startup_runner_uses_rich_status_display_with_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_settings_dir(tmp_path, with_operator_credential=True)
    captured_display: list[object] = []

    def fake_run(*_args: object, **kwargs: object) -> RunSummary:
        display = cast(RichStatusDisplay, kwargs["status_display"])
        captured_display.append(display)
        display.stop()
        return _fake_summary()

    monkeypatch.setattr("application_pipeline.orchestrator.run", fake_run)
    monkeypatch.setattr(
        "application_pipeline.maintenance.run_maintenance",
        lambda *_a, **_kw: None,
    )

    run_startup(
        StartupRequest(
            cwd=tmp_path,
            mode="run",
            no_judge=False,
            has_terminal=True,
        )
    )

    assert isinstance(captured_display[0], RichStatusDisplay)
