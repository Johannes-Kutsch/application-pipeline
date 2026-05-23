"""Deduplication Store — URL-tier + tuple-tier with alias write.

Single-writer module (Pi only, per ADR-0002): no cross-process locking.
``is_seen`` is intentionally side-effecting on tuple-tier hits — it writes
an alias entry under the new URL so subsequent runs short-circuit on the
cheap URL lookup. See ADR-0003; do not "fix" this back to a pure read.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, get_args, runtime_checkable

from application_pipeline.atomic_write import write_atomic
from application_pipeline.text import normalize

from .errors import DedupStoreError

if TYPE_CHECKING:
    from application_pipeline.extracts.card_store import CardStore

SeenStatus = Literal[
    "out_of_domain",
    "matched",
    "selected_by_judge",
    "enrich_failed",
    "external_redirect",
    "expired",
]

_LEGACY_STATUSES: frozenset[str] = frozenset(
    {"off_domain", "kept", "classified_in_domain"}
)
_KNOWN_STATUSES: frozenset[str] = frozenset(get_args(SeenStatus))
SeenResult = Literal["url_hit", "tuple_hit", "judge_pending", "miss"]
RunScopedSeenResult = Literal[
    "url_hit", "tuple_hit", "judge_pending", "run_hit", "miss"
]


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
    def __init__(
        self,
        path: Path,
        records: dict[str, dict[str, Any]],
        *,
        card_store: "CardStore | None" = None,
    ) -> None:
        self._path = path
        self._records = records
        self._tuple_index: dict[tuple[str, str, str], str] = self._build_tuple_index(
            records
        )
        self._lock = threading.Lock()
        self._in_run: set[str] | None = None
        self._card_store = card_store

    @staticmethod
    def _validate_record(url: str, record: dict[str, Any]) -> None:
        """Raise DedupStoreError if record is missing any required alias field."""
        for field in ("company_lc", "title_lc", "location_lc", "status", "first_seen"):
            if record.get(field) is None:
                raise DedupStoreError(
                    f"record for {url!r} has missing or null required field {field!r}"
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

    def is_seen(self, key: _SeenKey) -> RunScopedSeenResult:
        """Return which dedup tier matched ``key``; on tuple match, write alias.

        Side effect (per ADR-0003): when the URL tier misses but the
        ``(company_lc, title_lc, location_lc)`` tuple matches a prior entry,
        an alias entry is written under ``key.url`` carrying the original
        record's ``status`` and ``first_seen`` so future runs short-circuit
        on the cheap URL lookup. The return value is unaffected by the
        alias write.
        Returns ``run_hit`` only while a ``run_scope()`` context is active.
        """
        with self._lock:
            if self._in_run is not None and key.url in self._in_run:
                return "run_hit"

            if key.url in self._records:
                if self._records[key.url].get("status") == "matched":
                    return "judge_pending"
                return "url_hit"

            canonical_url = self._tuple_lookup(key)
            if canonical_url is not None:
                self._write_alias(key.url, canonical_url)
                return "tuple_hit"

            if self._in_run is not None:
                self._in_run.add(key.url)
            return "miss"

    def _mark(
        self,
        key: _SeenKey,
        status: SeenStatus,
        *,
        overwrite_if: SeenStatus | None = None,
    ) -> None:
        """Write a new record for key. Caller must hold ``self._lock``.

        If ``overwrite_if`` is given and the existing record has that status,
        the record is overwritten rather than skipped.
        """
        existing = self._records.get(key.url)
        if existing is not None and existing.get("status") != overwrite_if:
            return

        company_lc = normalize(key.company)
        title_lc = normalize(key.title)
        location_lc = normalize(key.location)
        record = {
            "canonical_url": key.url,
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

    def _delete_from_stores(self, url: str) -> None:
        if self._card_store is not None:
            self._card_store.delete(url)

    def mark_out_of_domain(self, key: _SeenKey) -> None:
        with self._lock:
            self._mark(key, "out_of_domain")
            self._delete_from_stores(key.url)

    def mark_selected_by_judge(self, key: _SeenKey) -> None:
        with self._lock:
            self._mark(key, "selected_by_judge", overwrite_if="matched")
            self._delete_from_stores(key.url)

    def mark_enrich_failed(self, key: _SeenKey) -> None:
        with self._lock:
            self._mark(key, "enrich_failed", overwrite_if="matched")
            self._delete_from_stores(key.url)

    def mark_expired(self, key: _SeenKey) -> None:
        with self._lock:
            prior = self._records.get(key.url)
            prior_status = prior.get("status") if prior else None
            if prior_status == "matched":
                self._mark(key, "expired", overwrite_if="matched")
                self._delete_from_stores(key.url)
            else:
                self._mark(key, "expired")

    def mark_matched(self, key: _SeenKey) -> None:
        with self._lock:
            self._mark(key, "matched")

    @contextmanager
    def run_scope(self) -> Generator[DeduplicationStore, None, None]:
        with self._lock:
            self._in_run = set()
        try:
            yield self
        finally:
            with self._lock:
                self._in_run = None

    def _write_alias(self, new_url: str, canonical_url: str) -> None:
        original = self._records[canonical_url]
        self._validate_record(canonical_url, original)
        record = {
            "canonical_url": original.get("canonical_url", canonical_url),
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
        payload = json.dumps(
            records, indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        try:
            write_atomic(self._path, payload)
        except OSError as exc:
            raise DedupStoreError(
                f"could not persist dedup store to {self._path}: {exc}"
            ) from exc


def load(
    path: Path,
    *,
    card_store: "CardStore | None" = None,
) -> DeduplicationStore:
    if not path.exists():
        return DeduplicationStore(path, {}, card_store=card_store)

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DedupStoreError(f"could not read dedup store at {path}: {exc}") from exc

    if not raw:
        raise DedupStoreError(
            f"dedup store at {path} is empty; delete the file to start fresh"
        )

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

    for url, record in data.items():
        if isinstance(record, dict):
            status = record.get("status")
            if status in _LEGACY_STATUSES:
                raise DedupStoreError(
                    f"dedup store at {path} contains legacy status {status!r} "
                    f"for {url!r}; wipe the store to start fresh "
                    f"(see wipe instruction in the store migration guide)"
                )
            if status is not None and status not in _KNOWN_STATUSES:
                raise DedupStoreError(
                    f"dedup store at {path} contains unknown status {status!r} "
                    f"for {url!r}; wipe the store to start fresh "
                    f"(see wipe instruction in the store migration guide)"
                )

    return DeduplicationStore(path, data, card_store=card_store)
