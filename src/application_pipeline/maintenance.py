from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

_LOG_TAIL_LINES = 10_000
_THIRTY_DAYS_SECONDS = 30 * 24 * 3600
_FAILURE_MAX_AGE_SECONDS = _THIRTY_DAYS_SECONDS
_AGENT_RUNTIME_LOG_MAX_AGE_SECONDS = _THIRTY_DAYS_SECONDS
_AGENT_RUNTIME_LOG_SUBDIRS = (
    Path("llm/agent-runtime/classify"),
    Path("llm/agent-runtime/judge"),
)


def run_maintenance(logs_dir: Path, failures_dir: Path) -> None:
    _truncate_logs(logs_dir)
    _delete_old_failures(failures_dir)


def _is_agent_runtime_log(logs_dir: Path, path: Path) -> bool:
    return path.suffix == ".log" and any(
        path.parent == logs_dir / subdir for subdir in _AGENT_RUNTIME_LOG_SUBDIRS
    )


def _truncate_logs(logs_dir: Path) -> None:
    if not logs_dir.is_dir():
        return
    runtime_cutoff = time.time() - _AGENT_RUNTIME_LOG_MAX_AGE_SECONDS
    for path in logs_dir.rglob("*"):
        try:
            if not path.is_file():
                continue
            if _is_agent_runtime_log(logs_dir=logs_dir, path=path):
                if os.path.getmtime(path) < runtime_cutoff:
                    path.unlink()
                    invocation_dir = path.with_suffix("")
                    if invocation_dir.is_dir():
                        shutil.rmtree(invocation_dir)
                continue
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
