from __future__ import annotations

import os
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
    _delete_old_agent_runtime_evidence(logs_dir)
    _truncate_logs(logs_dir)
    _delete_old_failures(failures_dir)


def _agent_runtime_subdirs(logs_dir: Path) -> tuple[Path, ...]:
    return tuple(logs_dir / subdir for subdir in _AGENT_RUNTIME_LOG_SUBDIRS)


def _is_under_agent_runtime(logs_dir: Path, path: Path) -> bool:
    return any(
        subdir == path or subdir in path.parents
        for subdir in _agent_runtime_subdirs(logs_dir)
    )


def _delete_old_agent_runtime_evidence(logs_dir: Path) -> None:
    """Delete old Agent Runtime log files older than 30 days."""
    cutoff = time.time() - _AGENT_RUNTIME_LOG_MAX_AGE_SECONDS
    for subdir in _agent_runtime_subdirs(logs_dir):
        if not subdir.is_dir():
            continue
        for evidence_path in subdir.iterdir():
            try:
                if not evidence_path.is_file():
                    continue
                if os.path.getmtime(evidence_path) < cutoff:
                    evidence_path.unlink()
            except Exception:
                pass


def _truncate_logs(logs_dir: Path) -> None:
    if not logs_dir.is_dir():
        return
    for path in logs_dir.rglob("*"):
        try:
            if not path.is_file():
                continue
            # Agent Runtime evidence is deleted whole, never tail-truncated.
            if _is_under_agent_runtime(logs_dir, path):
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
