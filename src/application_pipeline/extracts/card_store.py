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
    def __init__(self, path: Path, records: dict[int, dict[str, str]]) -> None:
        self._path = path
        self._records = records
        self._lock = threading.Lock()

    def get(self, key: int) -> CardExtract | None:
        with self._lock:
            record = self._records.get(key)
        if record is None:
            return None
        return CardExtract(header=record["header"], summary=record["summary"])

    def put(self, key: int, extract: CardExtract) -> None:
        with self._lock:
            new_records = dict(self._records)
            new_records[key] = {"header": extract.header, "summary": extract.summary}
            self._persist(new_records)
            self._records = new_records

    def delete(self, key: int) -> None:
        with self._lock:
            if key not in self._records:
                return
            new_records = {k: v for k, v in self._records.items() if k != key}
            self._persist(new_records)
            self._records = new_records

    def _persist(self, records: dict[int, dict[str, str]]) -> None:
        payload = json.dumps(
            records, indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        try:
            write_atomic(self._path, payload)
        except OSError as exc:
            raise ExtractStoreError(
                f"could not persist card store to {self._path}: {exc}"
            ) from exc


def _migrate_legacy_extracts(
    data: dict[str, dict[str, str]],
    url_to_id: dict[str, int],
) -> dict[int, dict[str, str]]:
    """Convert URL-keyed legacy extracts to integer-keyed format, dropping orphans."""
    records: dict[int, dict[str, str]] = {}
    for url, extract in data.items():
        listing_id = url_to_id.get(url)
        if listing_id is not None:
            records[listing_id] = extract
    return records


def load_card_store(
    path: Path,
    *,
    url_to_id: dict[str, int] | None = None,
) -> CardStore:
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

    # Detect and silently migrate legacy URL-keyed format.
    if data and not next(iter(data)).lstrip("-").isdigit():
        records = _migrate_legacy_extracts(data, url_to_id or {})
        store = CardStore(path, records)
        store._persist(records)
        return store

    try:
        records = {int(k): v for k, v in data.items()}
    except (ValueError, TypeError) as exc:
        raise ExtractStoreError(
            f"card store at {path} has non-integer key: {exc}"
        ) from exc

    return CardStore(path, records)
