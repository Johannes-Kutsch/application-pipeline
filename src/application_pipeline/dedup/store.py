"""Deduplication Store — URL-tier slice.

Single-writer module (Pi only, per ADR-0002): no cross-process locking.
Tuple-tier match and the alias write described in ADR-0004 are deferred
to a follow-up slice; this module currently only answers via URL.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from .errors import DedupStoreError

logger = logging.getLogger(__name__)

SeenStatus = Literal["off_domain", "kept"]


@runtime_checkable
class _SeenKey(Protocol):
    url: str
    company: str | None
    title: str | None
    city: str | None


def _lc(value: str | None) -> str | None:
    if value is None:
        return None
    return value.lower()


class DeduplicationStore:
    def __init__(self, path: Path, records: dict[str, dict[str, Any]]) -> None:
        self._path = path
        self._records = records

    def is_seen(self, key: _SeenKey) -> bool:
        if key.url in self._records:
            logger.debug("is_seen: url match for %s", key.url)
            return True
        return False

    def mark_seen(self, key: _SeenKey, status: SeenStatus) -> None:
        if key.url in self._records:
            logger.debug("mark_seen: no-op, url already recorded: %s", key.url)
            return

        record = {
            "company_lc": _lc(key.company),
            "title_lc": _lc(key.title),
            "city_lc": _lc(key.city),
            "status": status,
            "first_seen": date.today().isoformat(),
        }

        new_records = dict(self._records)
        new_records[key.url] = record
        self._persist(new_records)
        self._records = new_records
        logger.debug("mark_seen: recorded %s with status=%s", key.url, status)

    def _persist(self, records: dict[str, dict[str, Any]]) -> None:
        tmp = self._path.with_name(self._path.name + ".tmp")
        payload = json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self._path)


def load(path: Path) -> DeduplicationStore:
    if not path.exists():
        return DeduplicationStore(path, {})

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DedupStoreError(f"could not read dedup store at {path}: {exc}") from exc

    if not raw:
        return DeduplicationStore(path, {})

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DedupStoreError(
            f"dedup store at {path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise DedupStoreError(
            f"dedup store at {path} must be a JSON object, got {type(data).__name__}"
        )

    return DeduplicationStore(path, data)
