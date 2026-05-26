from __future__ import annotations

import collections
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.home() / ".env")


class _TailHandler(logging.Handler):
    def __init__(self, n: int = 20) -> None:
        super().__init__()
        self._buf: collections.deque[str] = collections.deque(maxlen=n)

    def emit(self, record: logging.LogRecord) -> None:
        self._buf.append(self.format(record))

    def tail(self) -> str:
        return "\n".join(self._buf)


logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
_tail = _TailHandler()
_tail.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
logging.getLogger().addHandler(_tail)


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

    if args and args[0] == "run":
        run_flags = set(args[1:])
        unknown = run_flags - {"--no-judge"}
        if unknown:
            print("usage: application-pipeline run [--no-judge]", file=sys.stderr)
            print("       application-pipeline init [--refresh]", file=sys.stderr)
            print("       application-pipeline compile-cv <dir>", file=sys.stderr)
            sys.exit(2)
        no_judge = "--no-judge" in run_flags
        cwd = Path.cwd()
        config_path = cwd / "application-pipeline" / "config.py"
        if not config_path.exists():
            print(
                f"no application-pipeline/config.py in {cwd}"
                " — did you forget to cd, or run init?",
                file=sys.stderr,
            )
            sys.exit(2)
    else:
        print("usage: application-pipeline run [--no-judge]", file=sys.stderr)
        print("       application-pipeline init [--refresh]", file=sys.stderr)
        print("       application-pipeline compile-cv <dir>", file=sys.stderr)
        sys.exit(2)

    from application_pipeline.parser_log import RunLog
    from application_pipeline.config import resolve_data_paths
    from application_pipeline.failure_report import write_failure
    from application_pipeline.orchestrator import current_stage, run
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
    try:
        summary = run(
            config_path, status_display=display, run_log=run_log, no_judge=no_judge
        )
    except Exception as exc:
        try:
            write_failure(
                current_stage.get(),
                exc,
                _tail.tail(),
                resolve_data_paths(home).failures_path,
            )
        except Exception:
            pass
        sys.exit(1)

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
        f"  claude_input_tokens={summary.claude_input_tokens}"
        f"  claude_output_tokens={summary.claude_output_tokens}"
        f"  claude_cache_read_tokens={summary.claude_cache_read_tokens}"
        f"  claude_cost_usd={summary.claude_cost_usd:.6f}"
        f"  duration={summary.duration_seconds:.1f}s"
    )


if __name__ == "__main__":
    main()
