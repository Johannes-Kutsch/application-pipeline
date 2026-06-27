from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from application_pipeline.config import resolve_data_paths
from application_pipeline.parser_log import RunLog
from application_pipeline.status_display import PlainStatusDisplay, RichStatusDisplay

StartupMode = Literal["run", "cron"]


@dataclass(frozen=True, slots=True)
class StartupRequest:
    cwd: Path
    mode: StartupMode
    no_judge: bool
    has_terminal: bool


def run_startup(request: StartupRequest) -> None:
    config_path = _require_config_path(request.cwd)
    _require_operator_credential(config_path.parent)

    if request.mode == "cron":
        from application_pipeline.init_cmd import init as _init

        _init(request.cwd, refresh=True)

    _execute_run(
        config_path,
        no_judge=request.no_judge,
        has_terminal=request.has_terminal,
    )


def _require_config_path(cwd: Path) -> Path:
    config_path = cwd / "application-pipeline" / "config.py"
    if not config_path.exists():
        from application_pipeline.init_cmd import missing_config_message

        print(missing_config_message(cwd), file=sys.stderr)
        sys.exit(2)
    return config_path


def _require_operator_credential(settings_dir: Path) -> None:
    from application_pipeline.operator_credential import (
        OperatorCredentialError,
        load_operator_credential,
    )

    try:
        load_operator_credential(settings_dir)
    except OperatorCredentialError as exc:
        print(f"startup failed — operator credential: {exc}", file=sys.stderr)
        sys.exit(2)


def _execute_run(config_path: Path, *, no_judge: bool, has_terminal: bool) -> None:
    from application_pipeline.maintenance import run_maintenance
    from application_pipeline.orchestrator import run

    home = config_path.parent
    run_log = RunLog(resolve_data_paths(home).logs_path)
    display = (
        RichStatusDisplay(run_log=run_log)
        if has_terminal
        else PlainStatusDisplay(run_log=run_log)
    )
    summary = run(
        config_path, status_display=display, run_log=run_log, no_judge=no_judge
    )

    print(
        f"run complete:"
        f"  discovered={summary.discovered}"
        f"  skipped={summary.skipped}"
        f"  prefilter_dropped={summary.prefilter_dropped}"
        f"  classifier_dropped={summary.classifier_dropped}"
        f"  written={summary.written}"
        f"  enrich_failed={summary.enrich_failed}"
        f"  errored={summary.errored}"
        f"  classify_items={summary.classify_items}"
        f"  duration={summary.duration_seconds:.1f}s"
    )

    data_paths = resolve_data_paths(home)
    try:
        run_maintenance(data_paths.logs_path, data_paths.failures_path)
    except Exception:
        pass
