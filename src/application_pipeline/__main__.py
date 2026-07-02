from __future__ import annotations

import logging
import sys
from pathlib import Path

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
        from application_pipeline.startup_runner import StartupRequest, run_startup

        run_startup(
            StartupRequest(
                cwd=Path.cwd(),
                mode="cron",
                no_judge=no_judge,
                has_terminal=sys.stdout.isatty(),
            )
        )
        return

    if args and args[0] == "run":
        run_flags = set(args[1:])
        unknown = run_flags - {"--no-judge"}
        if unknown:
            _print_usage()
            sys.exit(2)
        no_judge = "--no-judge" in run_flags
        from application_pipeline.startup_runner import StartupRequest, run_startup

        run_startup(
            StartupRequest(
                cwd=Path.cwd(),
                mode="run",
                no_judge=no_judge,
                has_terminal=sys.stdout.isatty(),
            )
        )
        return

    _print_usage()
    sys.exit(2)


def _print_usage() -> None:
    print("usage: application-pipeline cron [--no-judge]", file=sys.stderr)
    print("       application-pipeline run [--no-judge]", file=sys.stderr)
    print("       application-pipeline init [--refresh]", file=sys.stderr)
    print("       application-pipeline compile-cv <dir>", file=sys.stderr)


if __name__ == "__main__":
    main()
