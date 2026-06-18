"""Card Extract Store — v3 schema: {stable_id: {header, summary, body}}."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from application_pipeline.atomic_write import write_atomic

from .errors import ExtractStoreError


@dataclass(frozen=True)
class CardExtract:
    header: str
    summary: str
    body: str = ""


class CardStore:
    def __init__(self, path: Path, records: dict[int, CardExtract]) -> None:
        self._path = path
        self._records = records
        self._lock = threading.Lock()

    def get(self, key: int) -> CardExtract | None:
        with self._lock:
            record = self._records.get(key)
        return _copy_card_extract_or_none(record)

    def put(self, key: int, extract: CardExtract) -> None:
        with self._lock:
            new_records = dict(self._records)
            new_records[key] = _copy_card_extract(extract)
            self._write_records(new_records)

    def replace_body_if_present(self, key: int, body: str) -> bool:
        with self._lock:
            record = self._records.get(key)
            if record is None or not body:
                return False
            new_records = dict(self._records)
            new_records[key] = CardExtract(
                header=record.header,
                summary=record.summary,
                body=body,
            )
            self._write_records(new_records)
            return True

    def delete(self, key: int) -> None:
        with self._lock:
            if key not in self._records:
                return
            new_records = {k: v for k, v in self._records.items() if k != key}
            self._write_records(new_records)

    def _write_records(self, records: dict[int, CardExtract]) -> None:
        payload = self._encode_records(records)
        self._persist(payload)
        self._records = records

    def _encode_records(self, records: dict[int, CardExtract]) -> bytes:
        return json.dumps(
            _encode_card_store_records(records),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")

    def _persist(self, payload: bytes) -> None:
        try:
            write_atomic(self._path, payload)
        except OSError as exc:
            raise ExtractStoreError(
                f"could not persist card store to {self._path}: {exc}"
            ) from exc


def _decode_card_store_records(
    data: dict[str, Any], path: Path
) -> dict[int, CardExtract]:
    try:
        parsed = {int(k): v for k, v in data.items()}
    except (ValueError, TypeError) as exc:
        raise ExtractStoreError(
            f"card store at {path} has non-integer key: {exc}"
        ) from exc

    return {
        key: CardExtract(
            header=record["header"],
            summary=record["summary"],
            body=record.get("body", ""),
        )
        for key, record in parsed.items()
    }


def _encode_card_store_records(
    records: dict[int, CardExtract],
) -> dict[int, dict[str, str]]:
    return {
        key: {
            "header": record.header,
            "summary": record.summary,
            "body": record.body,
        }
        for key, record in records.items()
    }


def _copy_card_extract(record: CardExtract) -> CardExtract:
    return CardExtract(
        header=record.header,
        summary=record.summary,
        body=record.body,
    )


def _copy_card_extract_or_none(record: CardExtract | None) -> CardExtract | None:
    if record is None:
        return None
    return _copy_card_extract(record)


def load_card_store(
    path: Path,
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

    if data and not next(iter(data)).lstrip("-").isdigit():
        raise ExtractStoreError(
            f"card store at {path} uses legacy URL-keyed format; "
            f"delete the file to start fresh"
        )

    return CardStore(path, _decode_card_store_records(data, path))
