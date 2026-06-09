from __future__ import annotations

import importlib.metadata
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def _discover_tag() -> str | None:
    try:
        return importlib.metadata.version("application-pipeline")
    except importlib.metadata.PackageNotFoundError:
        return None


def _render(
    timestamp: str,
    stage: str,
    error: BaseException,
    log_tail: str,
    tag: str | None,
) -> str:
    heading = f"# Run failed at {timestamp}"
    if tag:
        heading += f" (tag {tag})"
    return (
        f"{heading}\n\n"
        f"**Stage:** {stage}\n"
        f"**Error:** {type(error).__qualname__}: {error!r}\n"
        f"**Last 20 log lines:**\n"
        f"```\n"
        f"{log_tail}\n"
        f"```\n"
    )


@dataclass(frozen=True)
class FailureReportWriter:
    failures_dir: Path

    def record_parser_dead(
        self,
        parser_id: str,
        error: BaseException,
        traceback_str: str,
    ) -> Path:
        return self.write_failure(
            stage=f"parser:{parser_id}",
            error=error,
            log_tail=traceback_str,
        )

    def write_failure(
        self,
        stage: str,
        error: BaseException,
        log_tail: str,
    ) -> Path:
        now = datetime.now(UTC)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        filename_ts = timestamp.replace(":", "-")

        self.failures_dir.mkdir(parents=True, exist_ok=True)

        target = self.failures_dir / f"{filename_ts}.md"
        tmp = target.with_name(target.name + ".tmp")

        body = _render(timestamp, stage, error, log_tail, _discover_tag())
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, target)

        return target


def write_failure(
    stage: str,
    error: BaseException,
    log_tail: str,
    failures_dir: Path,
) -> Path:
    return FailureReportWriter(failures_dir).write_failure(stage, error, log_tail)
