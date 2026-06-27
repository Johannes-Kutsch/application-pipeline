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
