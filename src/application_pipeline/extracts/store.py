"""Extract Store — keyed StructuredExtract persistence.

Single-writer module (Pi only, per ADR-0002): no cross-process locking.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from application_pipeline.llm.types import StructuredExtract

from .errors import ExtractStoreError


class ExtractStore:
    def __init__(self, path: Path, records: dict[str, dict[str, Any]]) -> None:
        self._path = path
        self._records = records
        self._lock = threading.Lock()

    def get(self, key: str) -> StructuredExtract | None:
        with self._lock:
            record = self._records.get(key)
        if record is None:
            return None
        return _from_record(record)

    def put(self, key: str, extract: StructuredExtract) -> None:
        with self._lock:
            new_records = dict(self._records)
            new_records[key] = _to_record(extract)
            self._persist(new_records)
            self._records = new_records

    def delete(self, key: str) -> None:
        with self._lock:
            if key not in self._records:
                return
            new_records = {k: v for k, v in self._records.items() if k != key}
            self._persist(new_records)
            self._records = new_records

    def _persist(self, records: dict[str, dict[str, Any]]) -> None:
        tmp = self._path.with_name(self._path.name + ".tmp")
        payload = json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False)
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            try:
                os.write(fd, payload.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self._path)
        except OSError as exc:
            raise ExtractStoreError(
                f"could not persist extract store to {self._path}: {exc}"
            ) from exc


def _to_record(extract: StructuredExtract) -> dict[str, Any]:
    return {
        "seniority": extract.seniority,
        "work_model": extract.work_model,
        "contract_type": extract.contract_type,
        "key_skills": extract.key_skills,
        "key_responsibilities": extract.key_responsibilities,
        "must_have_requirements": extract.must_have_requirements,
        "notable_caveats": extract.notable_caveats,
    }


def _from_record(record: dict[str, Any]) -> StructuredExtract:
    return StructuredExtract(
        seniority=record.get("seniority"),
        work_model=record.get("work_model"),
        contract_type=record.get("contract_type"),
        key_skills=record.get("key_skills", []),
        key_responsibilities=record.get("key_responsibilities", []),
        must_have_requirements=record.get("must_have_requirements", []),
        notable_caveats=record.get("notable_caveats", ""),
    )


def load(path: Path) -> ExtractStore:
    if not path.exists():
        return ExtractStore(path, {})

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExtractStoreError(
            f"could not read extract store at {path}: {exc}"
        ) from exc

    if not raw:
        raise ExtractStoreError(
            f"extract store at {path} is empty; delete the file to start fresh"
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractStoreError(
            f"extract store at {path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ExtractStoreError(
            f"extract store at {path} must be a JSON object, got {type(data).__name__}"
        )

    return ExtractStore(path, data)
