import json
import logging
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from application_pipeline import (
    DedupStoreError,
    DeduplicationStore,
    SeenResult,
    SeenStatus,
    load,
)
from application_pipeline.dedup import load as dedup_load


@dataclass
class StubLike:
    url: str
    company: str | None = "Acme"
    title: str | None = "Engineer"
    location: str | None = "Hamburg"


@dataclass
class PositionLike:
    url: str
    company: str | None = "Acme"
    title: str | None = "Engineer"
    location: str | None = "Hamburg"
    raw_description: str = "..."


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / ".seen.json"


@pytest.fixture
def store(store_path: Path) -> DeduplicationStore:
    return dedup_load(store_path)


def test_package_reexports_dedup_api() -> None:
    # load is the config loader; ensure dedup names are exposed
    assert callable(load)
    assert DeduplicationStore is not None
    assert DedupStoreError is not None
    assert SeenStatus is not None
    assert SeenResult is not None


def test_is_seen_miss_on_fresh_store(store: DeduplicationStore) -> None:
    assert store.is_seen(StubLike(url="https://example.com/1")) == "miss"


def test_mark_kept_then_is_seen_returns_url_hit(store: DeduplicationStore) -> None:
    stub = StubLike(url="https://example.com/1")
    store.mark_kept(stub)
    assert store.is_seen(stub) == "url_hit"


def test_mark_off_domain_persists_status(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/x")
    store.mark_off_domain(stub)

    assert store.is_seen(stub) == "url_hit"

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    record = on_disk["https://example.com/x"]
    assert record["status"] == "off_domain"
    assert record["company_lc"] == "acme"
    assert record["title_lc"] == "engineer"
    assert record["location_lc"] == "hamburg"
    assert record["first_seen"] == date.today().isoformat()


def test_mark_kept_persists_status(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/k")
    store.mark_kept(stub)

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk["https://example.com/k"]["status"] == "kept"


def test_mark_enrich_failed_persists_status(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/ef")
    store.mark_enrich_failed(stub)

    assert store.is_seen(stub) == "url_hit"

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk["https://example.com/ef"]["status"] == "enrich_failed"


def test_mark_external_redirect_persists_status(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/redir")
    store.mark_external_redirect(stub)

    assert store.is_seen(stub) == "url_hit"

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk["https://example.com/redir"]["status"] == "external_redirect"


def test_second_mark_same_url_is_silent_no_op(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/y")
    store.mark_kept(stub)
    first = json.loads(store_path.read_text(encoding="utf-8"))

    store.mark_off_domain(stub)
    second = json.loads(store_path.read_text(encoding="utf-8"))

    assert first == second
    assert second["https://example.com/y"]["status"] == "kept"


def test_first_seen_preserved_across_reload(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/keep")
    store.mark_kept(stub)

    reloaded = dedup_load(store_path)
    assert reloaded.is_seen(stub) == "url_hit"

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk["https://example.com/keep"]["first_seen"] == date.today().isoformat()


def test_missing_file_initialises_empty(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.json"
    store = dedup_load(path)
    assert store.is_seen(StubLike(url="https://example.com/none")) == "miss"


def test_zero_byte_file_raises_dedup_store_error(store_path: Path) -> None:
    store_path.write_bytes(b"")
    with pytest.raises(DedupStoreError, match="empty"):
        dedup_load(store_path)


@pytest.mark.parametrize(
    "content",
    ["not-json", "[]", '"hello"', "123", '{"trailing":'],
)
def test_malformed_file_raises(store_path: Path, content: str) -> None:
    store_path.write_text(content, encoding="utf-8")
    with pytest.raises(DedupStoreError):
        dedup_load(store_path)


def test_persist_oserror_raises_dedup_store_error(
    store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/initial")
    store.mark_kept(stub)
    before = store_path.read_bytes()

    import os as _os

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_os, "replace", boom)

    with pytest.raises(DedupStoreError) as exc_info:
        store.mark_kept(StubLike(url="https://example.com/new"))

    assert isinstance(exc_info.value.__cause__, OSError)
    assert store_path.read_bytes() == before


def test_persist_write_oserror_raises_dedup_store_error(
    store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = dedup_load(store_path)

    import os as _os

    def boom(fd: int, data: bytes) -> int:
        raise OSError("no space left on device")

    monkeypatch.setattr(_os, "write", boom)

    with pytest.raises(DedupStoreError) as exc_info:
        store.mark_kept(StubLike(url="https://example.com/x"))

    assert isinstance(exc_info.value.__cause__, OSError)


def test_alias_write_corrupt_record_raises_dedup_store_error(
    store_path: Path,
) -> None:
    # Seed a store with a record missing required fields (corrupt on-disk data)
    store_path.write_text(
        json.dumps(
            {
                "https://example.com/corrupt": {
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    # status and first_seen intentionally missing
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path)
    # A new URL with the same tuple will trigger alias write on the corrupt record
    with pytest.raises(DedupStoreError):
        store.is_seen(StubLike(url="https://other.example/new"))


@pytest.mark.parametrize(
    "obj",
    [
        StubLike(url="https://example.com/a"),
        PositionLike(url="https://example.com/a"),
    ],
)
def test_mark_kept_accepts_stub_and_position(store_path: Path, obj: object) -> None:
    store = dedup_load(store_path)
    store.mark_kept(obj)  # type: ignore[arg-type]
    assert store.is_seen(obj) == "url_hit"  # type: ignore[arg-type]


def test_no_debug_records_during_is_seen_and_mark_methods(
    store_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = dedup_load(store_path)
    stub_a = StubLike(url="https://example.com/a")
    stub_b = StubLike(url="https://example.com/b")

    with caplog.at_level(logging.DEBUG, logger="application_pipeline.dedup.store"):
        store.is_seen(stub_a)
        store.mark_kept(stub_a)
        store.is_seen(stub_a)
        store.mark_off_domain(stub_a)
        store.is_seen(stub_b)

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records == []


def test_handles_none_company_title_location(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(
        url="https://example.com/n", company=None, title=None, location=None
    )
    store.mark_kept(stub)

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    record = on_disk["https://example.com/n"]
    assert record["company_lc"] is None
    assert record["title_lc"] is None
    assert record["location_lc"] is None


def test_tuple_match_under_new_url_returns_tuple_hit(store_path: Path) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://bundesagentur.de/job/1")
    b = StubLike(url="https://stellen.hamburg/job/42")
    store.mark_kept(a)
    assert store.is_seen(b) == "tuple_hit"


def test_tuple_match_writes_alias_with_original_status_and_first_seen(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://bundesagentur.de/job/1")
    b = StubLike(url="https://stellen.hamburg/job/42")
    store.mark_kept(a)
    original = json.loads(store_path.read_text(encoding="utf-8"))[a.url]

    store.is_seen(b)

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert b.url in on_disk
    assert on_disk[b.url]["status"] == original["status"]
    assert on_disk[b.url]["first_seen"] == original["first_seen"]


def test_alias_first_seen_is_originals_not_today(
    store_path: Path,
) -> None:
    # Seed a store with a backdated original; alias must copy that date.
    store_path.write_text(
        json.dumps(
            {
                "https://bundesagentur.de/old": {
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "kept",
                    "first_seen": "2024-01-15",
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path)
    b = StubLike(url="https://stellen.hamburg/new")
    store.is_seen(b)
    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk[b.url]["first_seen"] == "2024-01-15"
    assert on_disk[b.url]["first_seen"] != date.today().isoformat()


def test_after_alias_reload_resolves_via_url_tier(store_path: Path) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://bundesagentur.de/x")
    b = StubLike(url="https://stellen.hamburg/y")
    store.mark_kept(a)
    store.is_seen(b)

    reloaded = dedup_load(store_path)
    # b.url is now in the URL dict; even if we changed the tuple fields, URL hits.
    different_tuple = StubLike(
        url=b.url, company="Other", title="Other", location="Other"
    )
    assert reloaded.is_seen(different_tuple) == "url_hit"


def test_tuple_match_case_insensitive(store_path: Path) -> None:
    store = dedup_load(store_path)
    store.mark_kept(
        StubLike(
            url="https://example.com/1",
            company="acme gmbh",
            title="engineer",
            location="hamburg",
        ),
    )
    assert (
        store.is_seen(
            StubLike(
                url="https://example.com/2",
                company="ACME GmbH",
                title="Engineer",
                location="Hamburg",
            )
        )
        == "tuple_hit"
    )


def test_tuple_match_collapses_internal_whitespace(store_path: Path) -> None:
    store = dedup_load(store_path)
    store.mark_kept(
        StubLike(
            url="https://example.com/1",
            company="ACME GmbH",
            title="Software Engineer",
            location="Hamburg",
        ),
    )
    assert (
        store.is_seen(
            StubLike(
                url="https://example.com/2",
                company="ACME  GmbH",
                title="Software   Engineer",
                location=" Hamburg ",
            )
        )
        == "tuple_hit"
    )


def test_tuple_lookup_skipped_when_field_none(store_path: Path) -> None:
    store = dedup_load(store_path)
    store.mark_kept(
        StubLike(
            url="https://example.com/1",
            company=None,
            title="Engineer",
            location="Hamburg",
        ),
    )
    # Different URL with same (None, title, location) must NOT match via tuple.
    assert (
        store.is_seen(
            StubLike(
                url="https://example.com/2",
                company=None,
                title="Engineer",
                location="Hamburg",
            )
        )
        == "miss"
    )


def test_tuple_lookup_skipped_when_field_empty_after_normalize(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    store.mark_kept(
        StubLike(
            url="https://example.com/1",
            company="   ",
            title="Engineer",
            location="Hamburg",
        ),
    )
    assert (
        store.is_seen(
            StubLike(
                url="https://example.com/2",
                company="",
                title="Engineer",
                location="Hamburg",
            )
        )
        == "miss"
    )


def test_none_company_url_match_still_works_on_second_is_seen(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(
        url="https://example.com/n", company=None, title="Engineer", location="Hamburg"
    )
    store.mark_kept(stub)
    assert store.is_seen(stub) == "url_hit"


def test_tuple_index_built_at_load_time(store_path: Path) -> None:
    store_path.write_text(
        json.dumps(
            {
                "https://example.com/seed": {
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "kept",
                    "first_seen": "2024-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path)
    assert (
        store.is_seen(
            StubLike(
                url="https://other.example/x",
                company="Acme",
                title="Engineer",
                location="Hamburg",
            )
        )
        == "tuple_hit"
    )


def test_round_trip_mix_of_originals_and_aliases(store_path: Path) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://a.example/1", company="Acme", title="Eng", location="HH")
    b = StubLike(url="https://b.example/1", company="Acme", title="Eng", location="HH")
    c = StubLike(
        url="https://a.example/2", company="Beta", title="PM", location="Berlin"
    )
    store.mark_kept(a)
    store.is_seen(b)  # writes alias under b.url
    store.mark_off_domain(c)

    before = json.loads(store_path.read_text(encoding="utf-8"))
    reloaded = dedup_load(store_path)
    assert reloaded.is_seen(a) == "url_hit"
    assert reloaded.is_seen(b) == "url_hit"
    assert reloaded.is_seen(c) == "url_hit"
    after = json.loads(store_path.read_text(encoding="utf-8"))
    assert before == after


def test_external_redirect_status_survives_reload(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/redir2")
    store.mark_external_redirect(stub)

    reloaded = dedup_load(store_path)
    assert reloaded.is_seen(stub) == "url_hit"


def test_concurrent_marks_produce_valid_store(store_path: Path) -> None:
    store = dedup_load(store_path)
    n_threads = 10
    marks_per_thread = 100
    errors: list[Exception] = []

    def worker(thread_id: int) -> None:
        try:
            for i in range(marks_per_thread):
                stub = StubLike(
                    url=f"https://example.com/t{thread_id}/i{i}",
                    company=f"Company{thread_id}",
                    title=f"Role{i}",
                    location="Hamburg",
                )
                store.mark_kept(stub)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"threads raised: {errors}"

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert isinstance(on_disk, dict)
    assert len(on_disk) == n_threads * marks_per_thread
