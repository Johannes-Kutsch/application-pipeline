"""Card Extract Store — v2 schema: {stable_id: {header, summary}}."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from application_pipeline.atomic_write import write_atomic

from .errors import ExtractStoreError


@dataclass(frozen=True)
class CardExtract:
    header: str
    summary: str


class CardStore:
    def __init__(self, path: Path, records: dict[str, dict[str, str]]) -> None:
        self._path = path
        self._records = records
        self._lock = threading.Lock()

    def get(self, key: str) -> CardExtract | None:
        with self._lock:
            record = self._records.get(key)
        if record is None:
            return None
        return CardExtract(header=record["header"], summary=record["summary"])

    def put(self, key: str, extract: CardExtract) -> None:
        with self._lock:
            new_records = dict(self._records)
            new_records[key] = {"header": extract.header, "summary": extract.summary}
            self._persist(new_records)
            self._records = new_records

    def delete(self, key: str) -> None:
        with self._lock:
            if key not in self._records:
                return
            new_records = {k: v for k, v in self._records.items() if k != key}
            self._persist(new_records)
            self._records = new_records

    def _persist(self, records: dict[str, dict[str, str]]) -> None:
        payload = json.dumps(
            records, indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        try:
            write_atomic(self._path, payload)
        except OSError as exc:
            raise ExtractStoreError(
                f"could not persist card store to {self._path}: {exc}"
            ) from exc


def load_card_store(path: Path) -> CardStore:
    if not path.exists():
        return CardStore(path, {})

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExtractStoreError(f"could not read card store at {path}: {exc}") from exc

    if not raw:
        raise ExtractStoreError(
            f"card store at {path} is empty; delete the file to start fresh"
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractStoreError(
            f"card store at {path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ExtractStoreError(
            f"card store at {path} must be a JSON object, got {type(data).__name__}"
        )

    return CardStore(path, data)
