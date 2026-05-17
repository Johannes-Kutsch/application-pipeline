from __future__ import annotations

import collections
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.home() / ".env")

from application_pipeline import parser_log  # noqa: E402
from application_pipeline.failure_report import write_failure  # noqa: E402
from application_pipeline.orchestrator import current_stage, run  # noqa: E402
from application_pipeline.status_display import PlainStatusDisplay, RichStatusDisplay  # noqa: E402


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

    if len(args) == 2 and args[0] == "init":
        from application_pipeline.init_cmd import init

        init(Path(args[1]))
        return

    if len(args) != 1:
        print("usage: python -m application_pipeline <config>", file=sys.stderr)
        print("       python -m application_pipeline init <dir>", file=sys.stderr)
        sys.exit(2)

    config_path = Path(args[0])
    display = RichStatusDisplay() if sys.stdout.isatty() else PlainStatusDisplay()
    try:
        parser_log.configure(config_path.parent / "logs")
        summary = run(config_path, status_display=display)
    except Exception as exc:
        try:
            write_failure(
                current_stage.get(),
                exc,
                _tail.tail(),
                config_path.resolve().parent / "failures",
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
        f"  green={summary.green}"
        f"  amber={summary.amber}"
        f"  red={summary.red}"
        f"  enrich_failed={summary.enrich_failed}"
        f"  external_redirects={summary.external_redirects}"
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
