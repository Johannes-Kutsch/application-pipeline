from __future__ import annotations

import logging
import sys
from pathlib import Path

from application_pipeline.config import ConfigError
from application_pipeline.dedup import DedupStoreError
from application_pipeline.llm import ExtractorUnreachableError
from application_pipeline.orchestrator import run
from application_pipeline.prompts import PromptError
from application_pipeline.results import ResultsFileError

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)

_FATAL = (
    ConfigError,
    PromptError,
    ExtractorUnreachableError,
    DedupStoreError,
    ResultsFileError,
)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m application_pipeline <config>", file=sys.stderr)
        sys.exit(2)

    config_path = Path(sys.argv[1])
    try:
        summary = run(config_path)
    except _FATAL:
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
        f"  duration={summary.duration_seconds:.1f}s"
    )


if __name__ == "__main__":
    main()
