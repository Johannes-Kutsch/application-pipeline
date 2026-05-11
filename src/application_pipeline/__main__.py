from __future__ import annotations

import collections
import logging
import sys
from pathlib import Path

from application_pipeline.config import ConfigError
from application_pipeline.dedup import DedupStoreError
from application_pipeline.failure_report import write_failure
from application_pipeline.llm import ExtractorUnreachableError
from application_pipeline.orchestrator import current_stage, run
from application_pipeline.prompts import PromptError
from application_pipeline.results import ResultsFileError

_FATAL = (
    ConfigError,
    PromptError,
    ExtractorUnreachableError,
    DedupStoreError,
    ResultsFileError,
)


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
_tail = _TailHandler()
_tail.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
logging.getLogger().addHandler(_tail)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m application_pipeline <config>", file=sys.stderr)
        sys.exit(2)

    config_path = Path(sys.argv[1])
    try:
        summary = run(config_path)
    except _FATAL as exc:
        try:
            write_failure(current_stage.get(), exc, _tail.tail(), Path("results"))
        except Exception:
            pass
        sys.exit(1)

    print(
        f"done"
        f"  discovered={summary.discovered}"
        f"  skipped={summary.skipped}"
        f"  prefilter_dropped={summary.prefilter_dropped}"
        f"  classifier_dropped={summary.classifier_dropped}"
        f"  written={summary.written}"
        f"  green={summary.green}"
        f"  amber={summary.amber}"
        f"  red={summary.red}"
        f"  enrich_failed={summary.enrich_failed}"
        f"  errored={summary.errored}"
        f"  duration={summary.duration_seconds:.1f}s"
    )


if __name__ == "__main__":
    main()
