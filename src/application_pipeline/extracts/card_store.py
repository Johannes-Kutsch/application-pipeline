"""Card Extract Store.

Accepted persisted record shapes:
- {stable_id: {header, summary, body}}
- {stable_id: {header, summary}} with body defaulting to ""
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from application_pipeline.atomic_write import write_atomic

from .errors import ExtractStoreError


@dataclass(frozen=True)
class CardExtract:
    header: str
    summary: str
    body: str = ""


class _PersistedCardRecord(TypedDict):
    header: str
    summary: str
    body: NotRequired[str]


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
) -> tuple[dict[int, CardExtract], bool]:
    try:
        parsed = {int(k): v for k, v in data.items()}
    except (ValueError, TypeError) as exc:
        raise ExtractStoreError(
            f"card store at {path} has non-integer key: {exc}"
        ) from exc

    saw_retired_v1 = False
    decoded_records: dict[int, CardExtract] = {}
    for key, record in parsed.items():
        match _classify_persisted_record(record, path, key):
            case "current_card":
                decoded_records[key] = _decode_card_record(record, path, key)
            case "retired_v1":
                saw_retired_v1 = True
            case "malformed":
                raise ExtractStoreError(
                    f"card store at {path} has invalid card record for key {key}: "
                    "expected object with header, summary, and optional body"
                )
    if saw_retired_v1 and decoded_records:
        raise ExtractStoreError(
            f"card store at {path} mixes current card records with retired v1 records"
        )
    return decoded_records, saw_retired_v1


def _classify_persisted_record(
    record: Any, path: Path, key: int
) -> Literal["current_card", "retired_v1", "malformed"]:
    if _record_presents_current_card_fields(record):
        _validate_persisted_card_record(record, path, key)
        return "current_card"
    if isinstance(record, dict):
        return "retired_v1"
    return "malformed"


def _record_presents_current_card_fields(record: Any) -> bool:
    return isinstance(record, dict) and any(
        field in record for field in ("header", "summary", "body")
    )


def _is_retired_v1_record(record: Any) -> bool:
    return isinstance(record, dict) and not _record_presents_current_card_fields(record)


def _validate_listing_id_keys(data: dict[str, Any], path: Path) -> None:
    for raw_key in data:
        try:
            int(raw_key)
        except (TypeError, ValueError) as exc:
            if isinstance(raw_key, str) and "://" in raw_key:
                raise ExtractStoreError(
                    f"card store at {path} uses legacy URL-keyed format; "
                    f"delete the file to start fresh"
                ) from exc
            raise ExtractStoreError(
                f"card store at {path} has non-integer key: {exc}"
            ) from exc


def _wipe_card_store_to_empty_object(path: Path) -> None:
    try:
        write_atomic(path, b"{}")
    except OSError as exc:
        raise ExtractStoreError(
            f"could not persist card store to {path}: {exc}"
        ) from exc


def _decode_card_record(record: Any, path: Path, key: int) -> CardExtract:
    persisted_record = _validate_persisted_card_record(record, path, key)
    body = persisted_record["body"] if "body" in persisted_record else ""
    return CardExtract(
        header=persisted_record["header"],
        summary=persisted_record["summary"],
        body=body,
    )


def _validate_persisted_card_record(
    record: Any, path: Path, key: int
) -> _PersistedCardRecord:
    if not isinstance(record, dict):
        raise ExtractStoreError(
            f"card store at {path} has invalid card record for key {key}: "
            "expected object with header, summary, and optional body"
        )

    header = record.get("header")
    summary = record.get("summary")
    body = record.get("body", "")
    if (
        not isinstance(header, str)
        or not isinstance(summary, str)
        or not isinstance(body, str)
    ):
        raise ExtractStoreError(
            f"card store at {path} has invalid card record for key {key}: "
            "expected object with header, summary, and optional body"
        )

    if "body" in record:
        return {
            "header": header,
            "summary": summary,
            "body": body,
        }
    return {
        "header": header,
        "summary": summary,
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

    _validate_listing_id_keys(data, path)
    records, saw_retired_v1 = _decode_card_store_records(data, path)
    if saw_retired_v1:
        _wipe_card_store_to_empty_object(path)
        return CardStore(path, {})

    return CardStore(path, records)
