from __future__ import annotations

import importlib.metadata
import os
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


def write_failure(
    stage: str,
    error: BaseException,
    log_tail: str,
    results_dir: Path,
) -> Path:
    now = datetime.now(UTC)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    filename_ts = timestamp.replace(":", "-")

    failures_dir = results_dir / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)

    target = failures_dir / f"{filename_ts}.md"
    tmp = target.with_name(target.name + ".tmp")

    body = _render(timestamp, stage, error, log_tail, _discover_tag())
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, target)

    return target
