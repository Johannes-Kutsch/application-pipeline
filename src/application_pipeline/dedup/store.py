"""Deduplication Store — URL-tier + tuple-tier + fuzzy-tier, integer-keyed records.

Single-writer module (Pi only, per ADR-0002): no cross-process locking.
``is_seen`` is intentionally side-effecting on tuple/fuzzy-tier hits — it prepends
the new URL to the canonical record's ``urls`` list and (for non-matched entries)
persists the update so subsequent runs short-circuit on the cheap URL lookup.
See ADR-0003.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, get_args, runtime_checkable

from application_pipeline.atomic_write import write_atomic
from application_pipeline.text import normalize

from .errors import DedupStoreError

# Gender markers stripped before fuzzy tokenization (ADR-0044).
_GENDER_MARKER_RE = re.compile(
    r"\(\s*(?:m\s*/\s*w\s*/\s*d(?:ivers)?|w\s*/\s*m\s*/\s*d(?:ivers)?|a\*)\s*\)",
    re.IGNORECASE,
)
_FUZZY_MIN_TOKENS = 4

if TYPE_CHECKING:
    from application_pipeline.extracts.card_store import CardStore
    from application_pipeline.parser_log import RunLog

SeenStatus = Literal[
    "out_of_domain",
    "matched",
    "selected_by_judge",
    "external_redirect",
    "expired",
    "pending",
]

_LEGACY_STATUSES: frozenset[str] = frozenset(
    {"off_domain", "kept", "classified_in_domain"}
)
# enrich_failed is retired but may still exist in seen.json; accept it on load.
_KNOWN_STATUSES: frozenset[str] = frozenset(get_args(SeenStatus)) | {"enrich_failed"}

SeenResult = Literal["url_hit", "tuple_hit", "fuzzy_hit", "judge_pending", "miss"]
RunScopedSeenKind = Literal[
    "url_hit", "tuple_hit", "fuzzy_hit", "judge_pending", "run_hit", "miss"
]


@dataclass
class RunScopedSeenResult:
    kind: RunScopedSeenKind
    listing_id: int


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


def _tokenize_title(title_lc: str) -> frozenset[str]:
    """Strip gender markers, split into tokens, return as frozenset."""
    stripped = _GENDER_MARKER_RE.sub("", title_lc)
    return frozenset(stripped.split())


def _fuzzy_subset(tokens_a: frozenset[str], tokens_b: frozenset[str]) -> bool:
    """Return True if shorter is a subset of longer and has >= _FUZZY_MIN_TOKENS tokens."""
    shorter, longer = (
        (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    )
    return len(shorter) >= _FUZZY_MIN_TOKENS and shorter <= longer


class DeduplicationStore:
    def __init__(
        self,
        path: Path,
        records: dict[int, dict[str, Any]],
        *,
        card_store: "CardStore | None" = None,
        cooldown_days: int = 30,
        run_log: "RunLog | None" = None,
    ) -> None:
        self._path = path
        self._records = records
        self._url_index: dict[str, int] = self._build_url_index(records)
        self._tuple_index: dict[tuple[str, str, str], int] = self._build_tuple_index(
            records
        )
        self._fuzzy_index: dict[tuple[str, str], list[tuple[frozenset[str], int]]] = (
            self._build_fuzzy_index(records)
        )
        self._lock = threading.Lock()
        self._in_run: set[int] | None = None
        self._card_store = card_store
        self._cooldown_days = cooldown_days
        self._run_log = run_log

    @property
    def _next_id(self) -> int:
        """One greater than the current maximum listing ID. Caller must hold self._lock."""
        return max(self._records.keys(), default=0) + 1

    @staticmethod
    def _validate_record(listing_id: int, record: dict[str, Any]) -> None:
        """Raise DedupStoreError if record is missing any required field."""
        for field in (
            "company_lc",
            "title_lc",
            "location_lc",
            "status",
            "status_last_changed",
        ):
            if record.get(field) is None:
                raise DedupStoreError(
                    f"record for listing {listing_id!r} has missing or null required field {field!r}"
                )

    @staticmethod
    def _build_url_index(records: dict[int, dict[str, Any]]) -> dict[str, int]:
        index: dict[str, int] = {}
        for listing_id, record in records.items():
            for url in record.get("urls", []):
                if isinstance(url, str):
                    index.setdefault(url, listing_id)
        return index

    @staticmethod
    def _build_tuple_index(
        records: dict[int, dict[str, Any]],
    ) -> dict[tuple[str, str, str], int]:
        index: dict[tuple[str, str, str], int] = {}
        for listing_id, record in records.items():
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
                index.setdefault((company_lc, title_lc, location_lc), listing_id)
        return index

    @staticmethod
    def _build_fuzzy_index(
        records: dict[int, dict[str, Any]],
    ) -> dict[tuple[str, str], list[tuple[frozenset[str], int]]]:
        index: dict[tuple[str, str], list[tuple[frozenset[str], int]]] = {}
        for listing_id, record in records.items():
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
                fkey = (company_lc, location_lc)
                tokens = _tokenize_title(title_lc)
                bucket = index.setdefault(fkey, [])
                bucket.append((tokens, listing_id))
        return index

    def _cooldown_expired(self, record: dict[str, Any]) -> bool:
        """Return True if status_last_changed is older than cooldown_days."""
        slc = record.get("status_last_changed")
        if not isinstance(slc, str):
            return False
        try:
            changed = date.fromisoformat(slc)
        except ValueError:
            return False
        return (date.today() - changed).days > self._cooldown_days

    def _tuple_key(self, key: _SeenKey) -> tuple[str, str, str] | None:
        company_lc = normalize(key.company)
        title_lc = normalize(key.title)
        location_lc = normalize(key.location)
        if company_lc is None or title_lc is None or location_lc is None:
            return None
        return (company_lc, title_lc, location_lc)

    def _tuple_lookup(self, key: _SeenKey) -> int | None:
        tkey = self._tuple_key(key)
        if tkey is None:
            return None
        return self._tuple_index.get(tkey)

    def _fuzzy_lookup(self, key: _SeenKey) -> int | None:
        """Return the listing_id of the first fuzzy-index entry that token-subset matches key."""
        company_lc = normalize(key.company)
        title_lc = normalize(key.title)
        location_lc = normalize(key.location)
        if not company_lc or not title_lc or not location_lc:
            return None
        fkey = (company_lc, location_lc)
        bucket = self._fuzzy_index.get(fkey)
        if bucket is None:
            return None
        key_tokens = _tokenize_title(title_lc)
        for stored_tokens, listing_id in bucket:
            if _fuzzy_subset(key_tokens, stored_tokens):
                return listing_id
        return None

    def _log_hit(
        self,
        event: str,
        key: _SeenKey,
        listing_id: int,
    ) -> None:
        """Write a JSONL hit event. Caller must hold ``self._lock``."""
        if self._run_log is None:
            return
        record = self._records.get(listing_id, {})
        canonical_url = record.get("urls", [None])[0]
        self._run_log.event(
            "pipeline_dedup",
            event,
            new_url=key.url,
            canonical_url=canonical_url,
            new_title=key.title,
            canonical_title=record.get("title_lc"),
        )

    def listing_id_for(self, url: str) -> int | None:
        """Return the listing_id for a URL, or None if not registered."""
        return self._url_index.get(url)

    def is_seen(self, key: _SeenKey) -> RunScopedSeenResult:
        """Return which dedup tier matched ``key``; on tuple/fuzzy match, update urls list.

        Tuple and fuzzy tiers are status-aware: a hit on a ``matched`` entry returns
        ``judge_pending`` (first hit in a run prepends the new URL to the canonical
        record's urls list in-memory; subsequent hits within the same ``run_scope``
        return ``run_hit``). Non-``matched`` hits prepend the new URL and persist.
        Returns ``run_hit`` only while a ``run_scope()`` context is active.
        """
        with self._lock:
            existing_id = self._url_index.get(key.url)

            if existing_id is not None:
                existing = self._records[existing_id]
                if existing.get("status") == "pending":
                    # Post-enrich path: fields may be backfilled; check for new tuple/fuzzy
                    # match against a different listing (exclude self-references).
                    match_id = self._tuple_lookup(key)
                    if match_id is not None and match_id != existing_id:
                        match_record = self._records[match_id]
                        match_status = match_record.get("status")
                        if match_status == "matched":
                            self._prepend_url(match_id, key.url, persist=False)
                            return RunScopedSeenResult("judge_pending", match_id)
                        if (
                            match_status == "selected_by_judge"
                            and self._cooldown_expired(match_record)
                        ):
                            return RunScopedSeenResult("judge_pending", match_id)
                        if match_status == "expired" and self._cooldown_expired(
                            match_record
                        ):
                            return RunScopedSeenResult("miss", existing_id)
                        self._log_hit("tuple_hit", key, match_id)
                        self._prepend_url(match_id, key.url, persist=True)
                        return RunScopedSeenResult("tuple_hit", match_id)
                    match_id = self._fuzzy_lookup(key)
                    if match_id is not None and match_id != existing_id:
                        match_status = self._records[match_id].get("status")
                        if match_status == "matched":
                            self._prepend_url(match_id, key.url, persist=False)
                            return RunScopedSeenResult("judge_pending", match_id)
                        self._log_hit("fuzzy_hit", key, match_id)
                        self._prepend_url(match_id, key.url, persist=True)
                        return RunScopedSeenResult("fuzzy_hit", match_id)
                    # No cross-listing match; refresh pending with backfilled fields
                    self._write_pending(key, listing_id=existing_id)
                    if self._in_run is not None and existing_id in self._in_run:
                        return RunScopedSeenResult("run_hit", existing_id)
                    return RunScopedSeenResult("url_hit", existing_id)
                else:
                    # URL already seen with a real (non-pending) status
                    if self._in_run is not None and existing_id in self._in_run:
                        return RunScopedSeenResult("run_hit", existing_id)
                    status = existing.get("status")
                    if status == "matched":
                        return RunScopedSeenResult("judge_pending", existing_id)
                    if status == "selected_by_judge" and self._cooldown_expired(
                        existing
                    ):
                        return RunScopedSeenResult("judge_pending", existing_id)
                    if status == "expired" and self._cooldown_expired(existing):
                        return RunScopedSeenResult("miss", existing_id)
                    return RunScopedSeenResult("url_hit", existing_id)

            # URL not yet registered — check tuple, fuzzy, then miss
            match_id = self._tuple_lookup(key)
            if match_id is not None:
                original_record = self._records[match_id]
                original_status = original_record.get("status")
                if original_status == "matched":
                    if self._in_run is not None and match_id in self._in_run:
                        return RunScopedSeenResult("run_hit", match_id)
                    self._prepend_url(match_id, key.url, persist=False)
                    if self._in_run is not None:
                        self._in_run.add(match_id)
                    return RunScopedSeenResult("judge_pending", match_id)
                if original_status == "selected_by_judge" and self._cooldown_expired(
                    original_record
                ):
                    self._prepend_url(match_id, key.url, persist=False)
                    if self._in_run is not None:
                        self._in_run.add(match_id)
                    return RunScopedSeenResult("judge_pending", match_id)
                if original_status == "expired" and self._cooldown_expired(
                    original_record
                ):
                    return RunScopedSeenResult("miss", self._next_id)
                self._log_hit("tuple_hit", key, match_id)
                self._prepend_url(match_id, key.url, persist=True)
                return RunScopedSeenResult("tuple_hit", match_id)

            match_id = self._fuzzy_lookup(key)
            if match_id is not None:
                original_status = self._records[match_id].get("status")
                if original_status == "matched":
                    if self._in_run is not None and match_id in self._in_run:
                        return RunScopedSeenResult("run_hit", match_id)
                    self._prepend_url(match_id, key.url, persist=False)
                    if self._in_run is not None:
                        self._in_run.add(match_id)
                    return RunScopedSeenResult("judge_pending", match_id)
                self._log_hit("fuzzy_hit", key, match_id)
                self._prepend_url(match_id, key.url, persist=True)
                return RunScopedSeenResult("fuzzy_hit", match_id)

            new_id = self._write_pending(key)
            if self._in_run is not None:
                self._in_run.add(new_id)
            return RunScopedSeenResult("miss", new_id)

    def _write_pending(self, key: _SeenKey, *, listing_id: int | None = None) -> int:
        """Write or refresh an in-memory pending entry for key. Caller must hold self._lock.

        Returns the listing_id used.
        """
        company_lc = normalize(key.company)
        title_lc = normalize(key.title)
        location_lc = normalize(key.location)
        if listing_id is None:
            listing_id = self._next_id
        existing = self._records.get(listing_id)
        urls: list[str] = list(existing.get("urls", [])) if existing is not None else []
        if key.url not in urls:
            urls.insert(0, key.url)
        self._records[listing_id] = {
            "urls": urls,
            "company_lc": company_lc,
            "title_lc": title_lc,
            "location_lc": location_lc,
            "status": "pending",
            "status_last_changed": date.today().isoformat(),
        }
        self._url_index[key.url] = listing_id
        if company_lc and title_lc and location_lc:
            self._tuple_index.setdefault(
                (company_lc, title_lc, location_lc), listing_id
            )
            fkey = (company_lc, location_lc)
            tokens = _tokenize_title(title_lc)
            bucket = self._fuzzy_index.setdefault(fkey, [])
            if not any(lid == listing_id for _, lid in bucket):
                bucket.append((tokens, listing_id))
        return listing_id

    def _prepend_url(self, listing_id: int, new_url: str, *, persist: bool) -> None:
        """Prepend new_url to the record's urls list and update the URL index.

        Caller must hold self._lock.
        persist=True: validate and write to disk (non-matched tuple/fuzzy hits).
        persist=False: update in-memory only (matched/judge_pending paths).
        """
        record = self._records[listing_id]
        if persist:
            self._validate_record(listing_id, record)
        urls: list[str] = list(record.get("urls", []))
        if new_url not in urls:
            urls.insert(0, new_url)
            record["urls"] = urls
        self._url_index[new_url] = listing_id
        if persist:
            new_records = dict(self._records)
            new_records[listing_id] = record
            self._persist(new_records)
            self._records = new_records

    def _mark(
        self,
        listing_id: int,
        stub: _SeenKey,
        status: SeenStatus,
        *,
        overwrite_if: SeenStatus | None = None,
    ) -> None:
        """Update a record's status and fields. Caller must hold self._lock.

        If ``overwrite_if`` is given and the existing record has that status,
        the record is overwritten rather than skipped.
        Pending entries are always overwritten.
        """
        existing = self._records.get(listing_id)
        if (
            existing is not None
            and existing.get("status") != overwrite_if
            and existing.get("status") != "pending"
        ):
            return

        company_lc = normalize(stub.company)
        title_lc = normalize(stub.title)
        location_lc = normalize(stub.location)

        if existing is not None:
            urls: list[str] = list(existing.get("urls", []))
            if stub.url not in urls:
                urls.insert(0, stub.url)
        else:
            urls = [stub.url]

        record = {
            "urls": urls,
            "company_lc": company_lc,
            "title_lc": title_lc,
            "location_lc": location_lc,
            "status": status,
            "status_last_changed": date.today().isoformat(),
        }

        new_records = dict(self._records)
        new_records[listing_id] = record
        self._persist(new_records)
        self._records = new_records

        for url in urls:
            self._url_index.setdefault(url, listing_id)
        if company_lc and title_lc and location_lc:
            self._tuple_index.setdefault(
                (company_lc, title_lc, location_lc), listing_id
            )
            fkey = (company_lc, location_lc)
            tokens = _tokenize_title(title_lc)
            bucket = self._fuzzy_index.setdefault(fkey, [])
            if not any(lid == listing_id for _, lid in bucket):
                bucket.append((tokens, listing_id))

    def _resolve_listing_id(
        self, key_or_listing_id: "_SeenKey | int", stub: "_SeenKey | None"
    ) -> "tuple[int, _SeenKey]":
        """Return (listing_id, stub) from either calling form. Caller must hold self._lock."""
        if isinstance(key_or_listing_id, int):
            assert stub is not None
            return key_or_listing_id, stub
        stub_key = key_or_listing_id
        lid = self._url_index.get(stub_key.url)
        if lid is None:
            lid = self._write_pending(stub_key)
        return lid, stub_key

    def _delete_from_stores(self, url: str) -> None:
        if self._card_store is not None:
            self._card_store.delete(url)

    def mark_out_of_domain(
        self,
        key_or_listing_id: "_SeenKey | int",
        stub: "_SeenKey | None" = None,
    ) -> None:
        with self._lock:
            listing_id, stub = self._resolve_listing_id(key_or_listing_id, stub)
            self._mark(listing_id, stub, "out_of_domain")
            self._delete_from_stores(stub.url)

    def mark_selected_by_judge(
        self,
        key_or_listing_id: "_SeenKey | int",
        stub: "_SeenKey | None" = None,
    ) -> None:
        with self._lock:
            listing_id, stub = self._resolve_listing_id(key_or_listing_id, stub)
            self._mark(listing_id, stub, "selected_by_judge", overwrite_if="matched")
            self._delete_from_stores(stub.url)

    def mark_expired(
        self,
        key_or_listing_id: "_SeenKey | int",
        stub: "_SeenKey | None" = None,
    ) -> None:
        with self._lock:
            listing_id, stub = self._resolve_listing_id(key_or_listing_id, stub)
            prior = self._records.get(listing_id)
            prior_status = prior.get("status") if prior else None
            if prior_status == "matched":
                self._mark(listing_id, stub, "expired", overwrite_if="matched")
                self._delete_from_stores(stub.url)
            elif prior_status == "expired":
                self._mark(listing_id, stub, "expired", overwrite_if="expired")
            else:
                self._mark(listing_id, stub, "expired")

    def mark_matched(
        self,
        key_or_listing_id: "_SeenKey | int",
        stub: "_SeenKey | None" = None,
    ) -> None:
        with self._lock:
            listing_id, stub = self._resolve_listing_id(key_or_listing_id, stub)
            self._mark(listing_id, stub, "matched")

    @contextmanager
    def run_scope(self) -> Generator[DeduplicationStore, None, None]:
        with self._lock:
            self._in_run = set()
        try:
            yield self
        finally:
            with self._lock:
                self._in_run = None
                self._evict_pending()

    def _evict_pending(self) -> None:
        """Remove in-memory pending entries that were never promoted. Caller must hold self._lock."""
        pending_ids = {
            lid for lid, rec in self._records.items() if rec.get("status") == "pending"
        }
        for lid in pending_ids:
            record = self._records.pop(lid)
            for url in record.get("urls", []):
                if self._url_index.get(url) == lid:
                    del self._url_index[url]
        self._tuple_index = {
            tkey: lid
            for tkey, lid in self._tuple_index.items()
            if lid not in pending_ids
        }
        self._fuzzy_index = {
            fkey: kept
            for fkey, entries in self._fuzzy_index.items()
            if (
                kept := [
                    (tokens, lid) for tokens, lid in entries if lid not in pending_ids
                ]
            )
        }

    def _persist(self, records: dict[int, dict[str, Any]]) -> None:
        to_write = {
            str(lid): rec
            for lid, rec in records.items()
            if rec.get("status") != "pending"
        }
        payload = json.dumps(
            to_write, indent=2, sort_keys=True, ensure_ascii=False
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
    cooldown_days: int = 30,
    run_log: "RunLog | None" = None,
) -> DeduplicationStore:
    if not path.exists():
        return DeduplicationStore(
            path,
            {},
            card_store=card_store,
            cooldown_days=cooldown_days,
            run_log=run_log,
        )

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

    records: dict[int, dict[str, Any]] = {}
    for key, record in data.items():
        try:
            listing_id = int(key)
        except (ValueError, TypeError):
            raise DedupStoreError(
                f"dedup store at {path} has non-integer key {key!r}; "
                f"wipe the store to start fresh"
            )
        if not isinstance(record, dict):
            raise DedupStoreError(
                f"dedup store at {path} has non-object record for key {key!r}"
            )
        records[listing_id] = record

    for record in records.values():
        if "first_seen" in record and "status_last_changed" not in record:
            record["status_last_changed"] = record.pop("first_seen")

    for listing_id, record in records.items():
        status = record.get("status")
        if status in _LEGACY_STATUSES:
            raise DedupStoreError(
                f"dedup store at {path} contains legacy status {status!r} "
                f"for listing {listing_id!r}; wipe the store to start fresh "
                f"(see wipe instruction in the store migration guide)"
            )
        if status is not None and status not in _KNOWN_STATUSES:
            raise DedupStoreError(
                f"dedup store at {path} contains unknown status {status!r} "
                f"for listing {listing_id!r}; wipe the store to start fresh "
                f"(see wipe instruction in the store migration guide)"
            )

    return DeduplicationStore(
        path,
        records,
        card_store=card_store,
        cooldown_days=cooldown_days,
        run_log=run_log,
    )
