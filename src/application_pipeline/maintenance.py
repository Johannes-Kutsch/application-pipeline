from __future__ import annotations

import os
import time
from pathlib import Path

_LOG_TAIL_LINES = 10_000
_FAILURE_MAX_AGE_SECONDS = 30 * 24 * 3600


def run_maintenance(logs_dir: Path, failures_dir: Path) -> None:
    _truncate_logs(logs_dir)
    _delete_old_failures(failures_dir)


def _truncate_logs(logs_dir: Path) -> None:
    if not logs_dir.is_dir():
        return
    for path in logs_dir.iterdir():
        if not path.is_file():
            continue
        try:
            lines = path.read_bytes().splitlines(keepends=True)
            if len(lines) > _LOG_TAIL_LINES:
                path.write_bytes(b"".join(lines[-_LOG_TAIL_LINES:]))
        except Exception:
            pass


def _delete_old_failures(failures_dir: Path) -> None:
    if not failures_dir.is_dir():
        return
    cutoff = time.time() - _FAILURE_MAX_AGE_SECONDS
    for path in failures_dir.iterdir():
        if path.suffix != ".md" or not path.is_file():
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                path.unlink()
        except Exception:
            pass
