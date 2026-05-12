"""Deduplication Store — URL-tier + tuple-tier with alias write.

Single-writer module (Pi only, per ADR-0002): no cross-process locking.
``is_seen`` is intentionally side-effecting on tuple-tier hits — it writes
an alias entry under the new URL so subsequent runs short-circuit on the
cheap URL lookup. See ADR-0004; do not "fix" this back to a pure read.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from application_pipeline.text import normalize

from .errors import DedupStoreError

logger = logging.getLogger(__name__)

SeenStatus = Literal["off_domain", "kept", "enrich_failed", "external_redirect"]
SeenResult = Literal["url_hit", "tuple_hit", "miss"]


@runtime_checkable
class _SeenKey(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def company(self) -> str | None: ...

    @property
    def title(self) -> str | None: ...

    @property
    def location(self) -> str | None: ...


class DeduplicationStore:
    def __init__(self, path: Path, records: dict[str, dict[str, Any]]) -> None:
        self._path = path
        self._records = records
        self._tuple_index: dict[tuple[str, str, str], str] = self._build_tuple_index(
            records
        )

    @staticmethod
    def _build_tuple_index(
        records: dict[str, dict[str, Any]],
    ) -> dict[tuple[str, str, str], str]:
        index: dict[tuple[str, str, str], str] = {}
        for url, record in records.items():
            company_lc = record.get("company_lc")
            title_lc = record.get("title_lc")
            location_lc = record.get("location_lc")
            if (
                isinstance(company_lc, str)
                and isinstance(title_lc, str)
                and isinstance(location_lc, str)
                and company_lc
                and title_lc
                and location_lc
            ):
                index.setdefault((company_lc, title_lc, location_lc), url)
        return index

    def _tuple_key(self, key: _SeenKey) -> tuple[str, str, str] | None:
        company_lc = normalize(key.company)
        title_lc = normalize(key.title)
        location_lc = normalize(key.location)
        if company_lc is None or title_lc is None or location_lc is None:
            return None
        return (company_lc, title_lc, location_lc)

    def _tuple_lookup(self, key: _SeenKey) -> str | None:
        tkey = self._tuple_key(key)
        if tkey is None:
            return None
        return self._tuple_index.get(tkey)

    def is_seen(self, key: _SeenKey) -> SeenResult:
        """Return which dedup tier matched ``key``; on tuple match, write alias.

        Side effect (per ADR-0004): when the URL tier misses but the
        ``(company_lc, title_lc, location_lc)`` tuple matches a prior entry,
        an alias entry is written under ``key.url`` carrying the original
        record's ``status`` and ``first_seen`` so future runs short-circuit
        on the cheap URL lookup. The return value is unaffected by the
        alias write.
        """
        if key.url in self._records:
            return "url_hit"

        canonical_url = self._tuple_lookup(key)
        if canonical_url is not None:
            self._write_alias(key.url, canonical_url)
            return "tuple_hit"

        return "miss"

    def mark_seen(self, key: _SeenKey, status: SeenStatus) -> None:
        if key.url in self._records:
            return

        company_lc = normalize(key.company)
        title_lc = normalize(key.title)
        location_lc = normalize(key.location)
        record = {
            "company_lc": company_lc,
            "title_lc": title_lc,
            "location_lc": location_lc,
            "status": status,
            "first_seen": date.today().isoformat(),
        }

        new_records = dict(self._records)
        new_records[key.url] = record
        self._persist(new_records)
        self._records = new_records
        if company_lc and title_lc and location_lc:
            self._tuple_index.setdefault((company_lc, title_lc, location_lc), key.url)

    def _write_alias(self, new_url: str, canonical_url: str) -> None:
        original = self._records[canonical_url]
        record = {
            "company_lc": original["company_lc"],
            "title_lc": original["title_lc"],
            "location_lc": original["location_lc"],
            "status": original["status"],
            "first_seen": original["first_seen"],
        }
        new_records = dict(self._records)
        new_records[new_url] = record
        self._persist(new_records)
        self._records = new_records

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
