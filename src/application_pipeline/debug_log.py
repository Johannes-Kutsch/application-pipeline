from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_logs_dir: Path | None = None


def configure(logs_dir: Path) -> None:
    global _logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    _logs_dir = logs_dir


def append(component_id: str, message: str) -> None:
    if _logs_dir is None:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_file = _logs_dir / f"{component_id}.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"{ts} {message}\n")
