from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

_logs_dir: Path | None = None
_default: RunLog | None = None


class RunLog:
    def __init__(self, logs_dir: Path) -> None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        self._logs_dir = logs_dir

    def _append(self, path: Path, text: str) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(text)

    def _ts(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def event(self, component_id: str, event_type: str, **fields: object) -> None:
        ts = self._ts()
        row: dict[str, object] = {"ts": ts, "event": event_type}
        row.update(fields)
        self._append(
            self._logs_dir / f"{component_id}.events.jsonl", json.dumps(row) + "\n"
        )

    def lifecycle(self, component_id: str, event_type: str, **fields: object) -> None:
        ts = self._ts()
        row: dict[str, object] = {
            "ts": ts,
            "event": event_type,
            "component": component_id,
        }
        row.update(fields)
        self._append(self._logs_dir / "lifecycle.jsonl", json.dumps(row) + "\n")

    def transcript(self, component_id: str, entry: Mapping[str, object]) -> None:
        self._append(
            self._logs_dir / f"{component_id}.transcripts.jsonl",
            json.dumps(entry) + "\n",
        )

    def traceback(self, component_id: str, traceback_str: str) -> None:
        ts = self._ts()
        text = f"=== {component_id}  {ts}  traceback ===\n{traceback_str}"
        if not traceback_str.endswith("\n"):
            text += "\n"
        self._append(self._logs_dir / "run.log", text)

    def summary(
        self,
        component_id: str,
        counts: Mapping[str, int | float | str],
        started_at: datetime,
    ) -> None:
        ts = started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = "\n".join(
            f"{k}: {v}" if isinstance(v, str) else f"{k}={v}" for k, v in counts.items()
        )
        self._append(
            self._logs_dir / "run.log",
            f"=== {component_id}  {ts}  summary ===\n\nSUMMARY OF SESSION {ts}\n{lines}\n\n\n",
        )


def _active() -> RunLog | None:
    """Return the active RunLog, lazily creating one when _logs_dir was set directly."""
    global _default
    if _logs_dir is None:
        return None
    if _default is None or _default._logs_dir is not _logs_dir:
        _default = RunLog(_logs_dir)
    return _default


def configure(logs_dir: Path) -> None:
    global _logs_dir, _default
    _default = RunLog(logs_dir)
    _logs_dir = logs_dir


def record(component_id: str, event_type: str, **fields: object) -> None:
    run_log = _active()
    if run_log is None:
        return
    run_log.event(component_id, event_type, **fields)


def record_lifecycle(component_id: str, event_type: str, **fields: object) -> None:
    run_log = _active()
    if run_log is None:
        return
    run_log.lifecycle(component_id, event_type, **fields)


def record_transcript(component_id: str, entry: Mapping[str, object]) -> None:
    run_log = _active()
    if run_log is None:
        return
    run_log.transcript(component_id, entry)


def record_traceback(component_id: str, traceback_str: str) -> None:
    run_log = _active()
    if run_log is None:
        return
    run_log.traceback(component_id, traceback_str)


def summarize(
    component_id: str,
    counts: Mapping[str, int | float | str],
    started_at: datetime,
) -> None:
    run_log = _active()
    if run_log is None:
        return
    run_log.summary(component_id, counts, started_at)
