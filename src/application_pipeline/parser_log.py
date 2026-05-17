from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

_logs_dir: Path | None = None


def configure(logs_dir: Path) -> None:
    global _logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    _logs_dir = logs_dir


def record(component_id: str, event_type: str, **fields: object) -> None:
    if _logs_dir is None:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row: dict[str, object] = {"ts": ts, "event": event_type}
    row.update(fields)
    events_file = _logs_dir / f"{component_id}.events.jsonl"
    with events_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def record_lifecycle(component_id: str, event_type: str, **fields: object) -> None:
    if _logs_dir is None:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row: dict[str, object] = {"ts": ts, "event": event_type, "component": component_id}
    row.update(fields)
    lifecycle_file = _logs_dir / "lifecycle.jsonl"
    with lifecycle_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def record_transcript(component_id: str, entry: Mapping[str, object]) -> None:
    if _logs_dir is None:
        return
    transcript_file = _logs_dir / f"{component_id}.transcripts.jsonl"
    with transcript_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def record_traceback(component_id: str, traceback_str: str) -> None:
    if _logs_dir is None:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_log = _logs_dir / "run.log"
    with run_log.open("a", encoding="utf-8") as f:
        f.write(f"=== {component_id}  {ts}  traceback ===\n")
        f.write(traceback_str)
        if not traceback_str.endswith("\n"):
            f.write("\n")


def summarize(
    component_id: str,
    counts: Mapping[str, int | float],
    started_at: datetime,
) -> None:
    if _logs_dir is None:
        return
    ts = started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    pairs = " ".join(f"{k}={v}" for k, v in counts.items())
    run_log = _logs_dir / "run.log"
    with run_log.open("a", encoding="utf-8") as f:
        f.write(f"=== {component_id}  {ts}  summary ===\n")
        f.write(f"\nSUMMARY OF SESSION {ts}\n{pairs}\n\n\n")
