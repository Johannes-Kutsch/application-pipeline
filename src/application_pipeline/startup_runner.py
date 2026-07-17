from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from application_pipeline.config import resolve_data_paths
from application_pipeline.parser_log import RunLog
from application_pipeline.run_metrics import RunSummary
from application_pipeline.status_display import PlainStatusDisplay, RichStatusDisplay

StartupMode = Literal["run", "cron"]


@dataclass(frozen=True, slots=True)
class StartupRequest:
    cwd: Path
    mode: StartupMode
    no_judge: bool
    has_terminal: bool


class CompletionSummary(Protocol):
    @property
    def discovered(self) -> int: ...

    @property
    def skipped(self) -> int: ...

    @property
    def prefilter_dropped(self) -> int: ...

    @property
    def classifier_dropped(self) -> int: ...

    @property
    def written(self) -> int: ...

    @property
    def enrich_failed(self) -> int: ...

    @property
    def errored(self) -> int: ...

    @property
    def classify_items(self) -> int: ...

    @property
    def duration_seconds(self) -> float: ...


def run_startup(request: StartupRequest) -> None:
    config_path = _require_config_path(request.cwd)
    home = config_path.parent
    _require_operator_credential(home)
    _require_opencode_cli()

    if request.mode == "cron":
        from application_pipeline.init_cmd import init as _init

        _init(request.cwd, refresh=True)

    summary = _execute_run(
        config_path,
        no_judge=request.no_judge,
        has_terminal=request.has_terminal,
    )
    print(render_completion_summary(summary))
    if isinstance(summary, RunSummary):
        _run_post_run_maintenance(home)


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


def _require_opencode_cli() -> None:
    if shutil.which("opencode") is None:
        print(
            "startup failed — opencode CLI not found on PATH"
            " (install via: npm install -g opencode-ai)",
            file=sys.stderr,
        )
        sys.exit(2)


def _execute_run(
    config_path: Path, *, no_judge: bool, has_terminal: bool
) -> CompletionSummary:
    from application_pipeline.orchestrator import run

    home = config_path.parent
    run_log = RunLog(resolve_data_paths(home).logs_path)
    display = (
        RichStatusDisplay(run_log=run_log)
        if has_terminal
        else PlainStatusDisplay(run_log=run_log)
    )
    return run(config_path, status_display=display, run_log=run_log, no_judge=no_judge)


def _run_post_run_maintenance(settings_dir: Path) -> None:
    from application_pipeline.maintenance import run_maintenance

    data_paths = resolve_data_paths(settings_dir)
    try:
        run_maintenance(data_paths.logs_path, data_paths.failures_path)
    except Exception:
        pass


def render_completion_summary(summary: CompletionSummary) -> str:
    return (
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
