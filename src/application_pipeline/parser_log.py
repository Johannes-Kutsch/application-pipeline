from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path


class RunLog:
    def __init__(self, logs_dir: Path) -> None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir = logs_dir
        # Parser threads and the orchestrator main thread both append to the
        # same per-component files. Without a lock, concurrent append-mode
        # writes interleave on Windows and corrupt the JSONL stream.
        self._write_lock = threading.Lock()

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    _LAYER_PREFIXES = ("parser_", "llm_", "pipeline_")

    def _component_path(self, component_id: str, suffix: str) -> Path:
        for prefix in self._LAYER_PREFIXES:
            if component_id.startswith(prefix):
                layer = prefix.rstrip("_")
                rest = component_id[len(prefix) :]
                subdir = self.logs_dir / layer
                subdir.mkdir(exist_ok=True)
                return subdir / f"{rest}.{suffix}"
        return self.logs_dir / f"{component_id}.{suffix}"

    def _append(self, path: Path, text: str) -> None:
        with self._write_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(text)

    def _write_jsonl(self, path: Path, row: Mapping[str, object]) -> None:
        self._append(path, json.dumps(dict(row)) + "\n")

    def event(self, component_id: str, event_type: str, **fields: object) -> None:
        self._write_jsonl(
            self._component_path(component_id, "events.jsonl"),
            {"ts": self._now(), "event": event_type, **fields},
        )

    def lifecycle(self, component_id: str, event_type: str, **fields: object) -> None:
        self._write_jsonl(
            self.logs_dir / "lifecycle.jsonl",
            {
                **fields,
                "ts": self._now(),
                "event": event_type,
                "component": component_id,
            },
        )

    def transcript(self, component_id: str, entry: Mapping[str, object]) -> None:
        self._write_jsonl(
            self._component_path(component_id, "transcripts.jsonl"), entry
        )

    def traceback(self, component_id: str, traceback_str: str) -> None:
        body = traceback_str if traceback_str.endswith("\n") else traceback_str + "\n"
        self._append(
            self.logs_dir / "run.log",
            f"=== {component_id}  {self._now()}  traceback ===\n{body}",
        )

    def summary(
        self,
        component_id: str,
        counts: Mapping[str, int | float | str],
        started_at: datetime,
    ) -> None:
        ts = started_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = "\n".join(
            f"{k}: {v}" if isinstance(v, str) else f"{k}={v}" for k, v in counts.items()
        )
        self._append(
            self.logs_dir / "run.log",
            f"=== {component_id}  {ts}  summary ===\n\nSUMMARY OF SESSION {ts}\n{lines}\n\n\n",
        )
