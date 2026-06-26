from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.home() / ".env")


logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> None:
    args = sys.argv[1:]

    if args and args[0] == "init":
        refresh = "--refresh" in args[1:]
        rest = [a for a in args[1:] if a != "--refresh"]
        if rest:
            print("usage: application-pipeline init [--refresh]", file=sys.stderr)
            sys.exit(2)
        from application_pipeline.init_cmd import init

        init(Path.cwd(), refresh=refresh)
        return

    if args and args[0] == "compile-cv" and len(args) == 2:
        from application_pipeline.compile_cv_cmd import compile_cv

        compile_cv(Path(args[1]))
        return

    if args and args[0] == "cron":
        cron_flags = set(args[1:])
        unknown = cron_flags - {"--no-judge"}
        if unknown:
            _print_usage()
            sys.exit(2)
        no_judge = "--no-judge" in cron_flags
        config_path = _require_config_path()
        _require_operator_credential(config_path.parent)

        from application_pipeline.init_cmd import init as _init

        _init(Path.cwd(), refresh=True)

        _execute_run(
            config_path,
            no_judge=no_judge,
        )
        return

    if args and args[0] == "run":
        run_flags = set(args[1:])
        unknown = run_flags - {"--no-judge"}
        if unknown:
            _print_usage()
            sys.exit(2)
        no_judge = "--no-judge" in run_flags
        config_path = _require_config_path()
        _require_operator_credential(config_path.parent)
        _execute_run(
            config_path,
            no_judge=no_judge,
        )
        return

    _print_usage()
    sys.exit(2)


def _print_usage() -> None:
    print("usage: application-pipeline cron [--no-judge]", file=sys.stderr)
    print("       application-pipeline run [--no-judge]", file=sys.stderr)
    print("       application-pipeline init [--refresh]", file=sys.stderr)
    print("       application-pipeline compile-cv <dir>", file=sys.stderr)


def _require_config_path() -> Path:
    cwd = Path.cwd()
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


def _execute_run(config_path: Path, *, no_judge: bool) -> None:
    from application_pipeline.parser_log import RunLog
    from application_pipeline.config import resolve_data_paths
    from application_pipeline.maintenance import run_maintenance
    from application_pipeline.orchestrator import run
    from application_pipeline.status_display import (
        PlainStatusDisplay,
        RichStatusDisplay,
    )

    home = config_path.parent
    run_log = RunLog(resolve_data_paths(home).logs_path)
    display = (
        RichStatusDisplay(run_log=run_log)
        if sys.stdout.isatty()
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


if __name__ == "__main__":
    main()
