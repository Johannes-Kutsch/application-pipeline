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
from application_pipeline.dedup import RunScopedSeenKind, load as dedup_load


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


def _find_record(on_disk: dict, url: str) -> dict:
    """Find a record containing url in its urls list."""
    return next(r for r in on_disk.values() if url in r.get("urls", []))


def _has_url(on_disk: dict, url: str) -> bool:
    """Check if any record contains url in its urls list."""
    return any(url in r.get("urls", []) for r in on_disk.values())


def test_dedup_store_creates_parent_dir_on_first_write(tmp_path: Path) -> None:
    path = tmp_path / ".runtime-data" / "seen.json"
    store = dedup_load(path)
    store.mark_out_of_domain(StubLike(url="https://example.com/x"))
    assert path.exists()
    assert path.parent.is_dir()


def test_package_reexports_dedup_api() -> None:
    # load is the config loader; ensure dedup names are exposed
    assert callable(load)
    assert DeduplicationStore is not None
    assert DedupStoreError is not None
    assert SeenStatus is not None
    assert SeenResult is not None


def test_is_seen_miss_on_fresh_store(store: DeduplicationStore) -> None:
    assert store.is_seen(StubLike(url="https://example.com/1")).kind == "miss"


def test_mark_selected_by_judge_then_is_seen_returns_url_hit(
    store: DeduplicationStore,
) -> None:
    stub = StubLike(url="https://example.com/1")
    store.mark_selected_by_judge(stub)
    assert store.is_seen(stub).kind == "url_hit"


def test_mark_out_of_domain_persists_status(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/x")
    store.mark_out_of_domain(stub)

    assert store.is_seen(stub).kind == "url_hit"

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    record = _find_record(on_disk, "https://example.com/x")
    assert record["status"] == "out_of_domain"
    assert record["urls"][0] == "https://example.com/x"
    assert record["company_lc"] == "acme"
    assert record["title_lc"] == "engineer"
    assert record["location_lc"] == "hamburg"
    assert record["status_last_changed"] == date.today().isoformat()


def test_mark_selected_by_judge_persists_status(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/k")
    store.mark_selected_by_judge(stub)

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert (
        _find_record(on_disk, "https://example.com/k")["status"] == "selected_by_judge"
    )


def test_second_mark_same_url_is_silent_no_op(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/y")
    store.mark_selected_by_judge(stub)
    first = json.loads(store_path.read_text(encoding="utf-8"))

    store.mark_out_of_domain(stub)
    second = json.loads(store_path.read_text(encoding="utf-8"))

    assert first == second
    assert (
        _find_record(second, "https://example.com/y")["status"] == "selected_by_judge"
    )


def test_status_last_changed_persisted_across_reload(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/keep")
    store.mark_selected_by_judge(stub)

    reloaded = dedup_load(store_path)
    assert reloaded.is_seen(stub).kind == "url_hit"

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert (
        _find_record(on_disk, "https://example.com/keep")["status_last_changed"]
        == date.today().isoformat()
    )


def test_missing_file_initialises_empty(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.json"
    store = dedup_load(path)
    assert store.is_seen(StubLike(url="https://example.com/none")).kind == "miss"


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
    store.mark_selected_by_judge(stub)
    before = store_path.read_bytes()

    import os as _os

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_os, "replace", boom)

    with pytest.raises(DedupStoreError) as exc_info:
        store.mark_selected_by_judge(StubLike(url="https://example.com/new"))

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
        store.mark_selected_by_judge(StubLike(url="https://example.com/x"))

    assert isinstance(exc_info.value.__cause__, OSError)


def test_alias_write_corrupt_record_raises_dedup_store_error(
    store_path: Path,
) -> None:
    # Seed a store with a record missing required fields (corrupt on-disk data)
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/corrupt"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    # status and status_last_changed intentionally missing
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
def test_mark_selected_by_judge_accepts_stub_and_position(
    store_path: Path, obj: object
) -> None:
    store = dedup_load(store_path)
    store.mark_selected_by_judge(obj)  # type: ignore[arg-type]
    assert store.is_seen(obj).kind == "url_hit"  # type: ignore[arg-type]


def test_no_debug_records_during_is_seen_and_mark_methods(
    store_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = dedup_load(store_path)
    stub_a = StubLike(url="https://example.com/a")
    stub_b = StubLike(url="https://example.com/b")

    with caplog.at_level(logging.DEBUG, logger="application_pipeline.dedup.store"):
        store.is_seen(stub_a)
        store.mark_selected_by_judge(stub_a)
        store.is_seen(stub_a)
        store.mark_out_of_domain(stub_a)
        store.is_seen(stub_b)

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records == []


def test_handles_none_company_title_location(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(
        url="https://example.com/n", company=None, title=None, location=None
    )
    store.mark_selected_by_judge(stub)

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    record = _find_record(on_disk, "https://example.com/n")
    assert record["company_lc"] is None
    assert record["title_lc"] is None
    assert record["location_lc"] is None


def test_tuple_match_under_new_url_returns_tuple_hit(store_path: Path) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://bundesagentur.de/job/1")
    b = StubLike(url="https://stellen.hamburg/job/42")
    store.mark_selected_by_judge(a)
    assert store.is_seen(b).kind == "tuple_hit"


def test_tuple_match_writes_alias_with_original_status_and_status_last_changed(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://bundesagentur.de/job/1")
    b = StubLike(url="https://stellen.hamburg/job/42")
    store.mark_selected_by_judge(a)
    original_disk = json.loads(store_path.read_text(encoding="utf-8"))
    original = _find_record(original_disk, a.url)
    store.is_seen(b)
    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert _has_url(on_disk, b.url)
    record = _find_record(on_disk, b.url)
    assert record["status"] == original["status"]
    assert record["status_last_changed"] == original["status_last_changed"]
    assert a.url in record["urls"]


def test_alias_status_last_changed_is_originals_not_today(
    store_path: Path,
) -> None:
    # Seed a store with an out_of_domain record (no cooldown); alias must copy its date.
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://bundesagentur.de/old"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "out_of_domain",
                    "status_last_changed": "2024-01-15",
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path)
    b = StubLike(url="https://stellen.hamburg/new")
    store.is_seen(b)
    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert _find_record(on_disk, b.url)["status_last_changed"] == "2024-01-15"
    assert (
        _find_record(on_disk, b.url)["status_last_changed"] != date.today().isoformat()
    )


def test_load_silently_migrates_legacy_first_seen(
    store_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/legacy"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "out_of_domain",
                    "first_seen": "2024-01-15",
                }
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        store = dedup_load(store_path)
    assert store.is_seen(StubLike(url="https://example.com/legacy")).kind == "url_hit"
    assert caplog.records == []


def test_after_alias_reload_resolves_via_url_tier(store_path: Path) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://bundesagentur.de/x")
    b = StubLike(url="https://stellen.hamburg/y")
    store.mark_selected_by_judge(a)
    store.is_seen(b)

    reloaded = dedup_load(store_path)
    # b.url is now in the URL dict; even if we changed the tuple fields, URL hits.
    different_tuple = StubLike(
        url=b.url, company="Other", title="Other", location="Other"
    )
    assert reloaded.is_seen(different_tuple).kind == "url_hit"


def test_tuple_match_case_insensitive(store_path: Path) -> None:
    store = dedup_load(store_path)
    store.mark_selected_by_judge(
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
        ).kind
        == "tuple_hit"
    )


def test_tuple_match_collapses_internal_whitespace(store_path: Path) -> None:
    store = dedup_load(store_path)
    store.mark_selected_by_judge(
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
        ).kind
        == "tuple_hit"
    )


def test_tuple_lookup_skipped_when_field_none(store_path: Path) -> None:
    store = dedup_load(store_path)
    store.mark_selected_by_judge(
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
        ).kind
        == "miss"
    )


def test_tuple_lookup_skipped_when_field_empty_after_normalize(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    store.mark_selected_by_judge(
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
        ).kind
        == "miss"
    )


def test_none_company_url_match_still_works_on_second_is_seen(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(
        url="https://example.com/n", company=None, title="Engineer", location="Hamburg"
    )
    store.mark_selected_by_judge(stub)
    assert store.is_seen(stub).kind == "url_hit"


def test_tuple_index_built_at_load_time(store_path: Path) -> None:
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/seed"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "out_of_domain",
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
        ).kind
        == "tuple_hit"
    )


def test_round_trip_mix_of_originals_and_aliases(store_path: Path) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://a.example/1", company="Acme", title="Eng", location="HH")
    b = StubLike(url="https://b.example/1", company="Acme", title="Eng", location="HH")
    c = StubLike(
        url="https://a.example/2", company="Beta", title="PM", location="Berlin"
    )
    store.mark_selected_by_judge(a)
    store.is_seen(b)  # writes alias under b.url
    store.mark_out_of_domain(c)

    before = json.loads(store_path.read_text(encoding="utf-8"))
    reloaded = dedup_load(store_path)
    assert reloaded.is_seen(a).kind == "url_hit"
    assert reloaded.is_seen(b).kind == "url_hit"
    assert reloaded.is_seen(c).kind == "url_hit"
    after = json.loads(store_path.read_text(encoding="utf-8"))
    assert before == after


def test_external_redirect_status_survives_reload(store_path: Path) -> None:
    # Write external_redirect status directly — mark_external_redirect() was removed
    # (ADR-0042: v2 pipeline no longer follows redirects), but the status value stays
    # so existing .seen.json entries continue to short-circuit.
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/redir2"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "external_redirect",
                    "first_seen": "2024-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    reloaded = dedup_load(store_path)
    assert (
        reloaded.is_seen(StubLike(url="https://example.com/redir2")).kind == "url_hit"
    )


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
                store.mark_selected_by_judge(stub)
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


def test_mark_matched_then_is_seen_returns_judge_pending(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/cid")
    store.mark_matched(stub)
    assert store.is_seen(stub).kind == "judge_pending"


def test_mark_matched_then_mark_selected_by_judge_returns_url_hit(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/cid3")
    store.mark_matched(stub)
    store.mark_selected_by_judge(stub)

    assert store.is_seen(stub).kind == "url_hit"

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert (
        _find_record(on_disk, "https://example.com/cid3")["status"]
        == "selected_by_judge"
    )


def test_mark_matched_persists_correct_record(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/cid2")
    store.mark_matched(stub)

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    record = _find_record(on_disk, "https://example.com/cid2")
    assert record["status"] == "matched"
    assert record["urls"][0] == "https://example.com/cid2"
    assert record["status_last_changed"] == date.today().isoformat()
    assert record["company_lc"] == "acme"
    assert record["title_lc"] == "engineer"
    assert record["location_lc"] == "hamburg"


def test_non_legacy_statuses_still_return_url_hit(store_path: Path) -> None:
    for status in ("enrich_failed", "external_redirect"):
        store_path.write_text(
            json.dumps(
                {
                    "1": {
                        "urls": ["https://example.com/legacy"],
                        "company_lc": "acme",
                        "title_lc": "engineer",
                        "location_lc": "hamburg",
                        "status": status,
                        "first_seen": "2024-01-01",
                    }
                }
            ),
            encoding="utf-8",
        )
        store = dedup_load(store_path)
        assert (
            store.is_seen(StubLike(url="https://example.com/legacy")).kind == "url_hit"
        )


def test_legacy_status_raises_on_load(store_path: Path) -> None:
    for status in ("off_domain", "kept", "classified_in_domain"):
        store_path.write_text(
            json.dumps(
                {
                    "1": {
                        "urls": ["https://example.com/legacy"],
                        "company_lc": "acme",
                        "title_lc": "engineer",
                        "location_lc": "hamburg",
                        "status": status,
                        "first_seen": "2024-01-01",
                    }
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(DedupStoreError, match=status):
            dedup_load(store_path)


def test_tuple_hit_on_matched_returns_judge_pending_directly(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://example.com/original")
    b = StubLike(url="https://example.com/new-url")
    store.mark_matched(a)
    assert store.is_seen(b).kind == "judge_pending"


# ---------------------------------------------------------------------------
# run_scope() — in-run URL tier
# ---------------------------------------------------------------------------


def test_run_scope_first_is_seen_returns_miss(store: DeduplicationStore) -> None:
    with store.run_scope() as scope:
        assert scope.is_seen(StubLike(url="https://example.com/new")).kind == "miss"


def test_run_scope_second_is_seen_same_url_returns_run_hit(
    store: DeduplicationStore,
) -> None:
    with store.run_scope() as scope:
        scope.is_seen(StubLike(url="https://example.com/a"))
        assert scope.is_seen(StubLike(url="https://example.com/a")).kind == "run_hit"


def test_run_scope_run_hit_does_not_mutate_persistent_store(
    store_path: Path, store: DeduplicationStore
) -> None:
    stub = StubLike(url="https://example.com/a")
    with store.run_scope() as scope:
        scope.is_seen(stub)  # miss → adds to in-run set
        scope.is_seen(stub)  # run_hit → must not write to disk

    assert not store_path.exists()


def test_run_scope_url_and_tuple_hits_do_not_populate_in_run_set(
    store: DeduplicationStore,
) -> None:
    a = StubLike(url="https://example.com/original")
    b = StubLike(url="https://example.com/alias")
    store.mark_selected_by_judge(a)
    with store.run_scope() as scope:
        scope.is_seen(a)  # url_hit — must not land in in-run set
        scope.is_seen(b)  # tuple_hit — must not land in in-run set
        assert scope.is_seen(a).kind == "url_hit"
        # b is now an alias in the persistent store → url_hit on second call
        assert scope.is_seen(b).kind == "url_hit"


def test_run_scope_re_entering_starts_fresh(store: DeduplicationStore) -> None:
    stub = StubLike(url="https://example.com/fresh")
    with store.run_scope() as scope1:
        scope1.is_seen(stub)  # miss → added to in-run set
        assert scope1.is_seen(stub).kind == "run_hit"

    with store.run_scope() as scope2:
        # persistent store was not mutated; in-run set is fresh
        assert scope2.is_seen(stub).kind == "miss"


def test_run_scoped_seen_result_has_six_variants() -> None:
    from typing import get_args

    args = get_args(RunScopedSeenKind)
    assert set(args) == {
        "url_hit",
        "tuple_hit",
        "fuzzy_hit",
        "judge_pending",
        "run_hit",
        "miss",
    }


def test_seen_result_includes_fuzzy_hit() -> None:
    from typing import get_args

    args = get_args(SeenResult)
    assert "fuzzy_hit" in args


def test_run_scope_concurrent_is_seen(store: DeduplicationStore) -> None:
    n_threads = 10
    n_per_thread = 50
    errors: list[Exception] = []
    results: list[str] = []
    lock = threading.Lock()

    with store.run_scope() as scope:

        def worker(thread_id: int) -> None:
            try:
                for i in range(n_per_thread):
                    stub = StubLike(url=f"https://example.com/t{thread_id}/i{i}")
                    r = scope.is_seen(stub)
                    with lock:
                        results.append(r.kind)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert errors == [], f"threads raised: {errors}"
    assert all(r in {"miss", "run_hit", "url_hit", "tuple_hit"} for r in results)


# ---------------------------------------------------------------------------
# mark_expired
# ---------------------------------------------------------------------------


def test_tuple_hit_on_matched_updates_in_memory_url_and_title(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    # Same tuple, different URLs — b is a re-discovery of a's listing under a new URL
    a = StubLike(url="https://example.com/original")
    b = StubLike(url="https://example.com/new-url")
    store.mark_matched(a)
    store.is_seen(b)  # judge_pending — should update canonical record's url/title

    # Trigger a persist via a different mark to observe in-memory state on disk
    store.mark_out_of_domain(StubLike(url="https://example.com/other", company="Other"))
    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    canonical_record = _find_record(on_disk, "https://example.com/original")
    assert canonical_record["urls"][0] == b.url
    # company, location, status, status_last_changed preserved from original
    assert canonical_record["company_lc"] == "acme"
    assert canonical_record["location_lc"] == "hamburg"
    assert canonical_record["status"] == "matched"


def test_second_tuple_hit_on_matched_within_run_scope_returns_run_hit(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://example.com/original")
    b = StubLike(url="https://example.com/new-url-1")
    c = StubLike(url="https://example.com/new-url-2")
    store.mark_matched(a)
    with store.run_scope() as scope:
        assert scope.is_seen(b).kind == "judge_pending"
        assert scope.is_seen(c).kind == "run_hit"


def test_tuple_hit_on_out_of_domain_still_returns_tuple_hit(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://example.com/original")
    b = StubLike(url="https://example.com/new-url")
    store.mark_out_of_domain(a)
    assert store.is_seen(b).kind == "tuple_hit"


def test_tuple_hit_on_selected_by_judge_still_returns_tuple_hit(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://example.com/original")
    b = StubLike(url="https://example.com/new-url")
    store.mark_matched(a)
    store.mark_selected_by_judge(a)
    assert store.is_seen(b).kind == "tuple_hit"


def test_tuple_hit_on_non_matched_writes_alias(store_path: Path) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://example.com/original")
    b = StubLike(url="https://example.com/new-url")
    store.mark_out_of_domain(a)
    store.is_seen(b)  # tuple_hit on out_of_domain — alias write should fire
    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert _has_url(on_disk, b.url)


def test_tuple_hit_on_matched_does_not_write_alias(store_path: Path) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://example.com/original")
    b = StubLike(url="https://example.com/new-url")
    store.mark_matched(a)
    store.is_seen(b)  # judge_pending — NO alias write for matched status
    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert not _has_url(on_disk, b.url)


def test_mark_expired_persists_status(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/exp")
    store.mark_expired(stub)

    assert store.is_seen(stub).kind == "url_hit"

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    record = _find_record(on_disk, "https://example.com/exp")
    assert record["status"] == "expired"
    assert record["urls"][0] == "https://example.com/exp"
    assert record["company_lc"] == "acme"
    assert record["title_lc"] == "engineer"
    assert record["location_lc"] == "hamburg"
    assert record["status_last_changed"] == date.today().isoformat()


def test_expired_status_survives_reload(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/exp-reload")
    store.mark_expired(stub)

    reloaded = dedup_load(store_path)
    assert reloaded.is_seen(stub).kind == "url_hit"

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert (
        _find_record(on_disk, "https://example.com/exp-reload")["status"] == "expired"
    )


def test_mark_expired_on_already_expired_refreshes_status_last_changed(
    store_path: Path,
) -> None:
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/re-expired"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "expired",
                    "status_last_changed": "2020-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/re-expired")
    store.mark_expired(stub)

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert (
        _find_record(on_disk, "https://example.com/re-expired")["status"] == "expired"
    )
    assert (
        _find_record(on_disk, "https://example.com/re-expired")["status_last_changed"]
        == date.today().isoformat()
    )


def test_expired_status_in_seen_json_does_not_raise_on_load(store_path: Path) -> None:
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/exp"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "expired",
                    "status_last_changed": date.today().isoformat(),
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path)
    assert store.is_seen(StubLike(url="https://example.com/exp")).kind == "url_hit"


def test_unknown_status_raises_on_load(store_path: Path) -> None:
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/unk"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "totally_unknown_status",
                    "first_seen": "2025-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(DedupStoreError, match="totally_unknown_status"):
        dedup_load(store_path)


# ---------------------------------------------------------------------------
# pending entry on miss
# ---------------------------------------------------------------------------


def test_miss_writes_pending_so_same_tuple_different_url_returns_tuple_hit(
    store: DeduplicationStore,
) -> None:
    a = StubLike(url="https://example.com/a")
    b = StubLike(url="https://example.com/b")
    assert store.is_seen(a).kind == "miss"
    assert store.is_seen(b).kind == "tuple_hit"


def test_miss_in_run_scope_second_same_url_returns_run_hit(
    store: DeduplicationStore,
) -> None:
    stub = StubLike(url="https://example.com/x")
    with store.run_scope() as scope:
        assert scope.is_seen(stub).kind == "miss"
        assert scope.is_seen(stub).kind == "run_hit"


def test_pending_entry_not_written_to_disk(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/pending")
    store.is_seen(stub)
    assert not store_path.exists()


@pytest.mark.parametrize(
    "mark_method,expected_status",
    [
        ("mark_out_of_domain", "out_of_domain"),
        ("mark_matched", "matched"),
        ("mark_expired", "expired"),
    ],
)
def test_mark_overwrites_pending_and_persists_real_status(
    store_path: Path, mark_method: str, expected_status: str
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/q")
    store.is_seen(stub)  # creates pending entry

    getattr(store, mark_method)(stub)

    assert store.is_seen(stub).kind in ("url_hit", "judge_pending")
    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert _find_record(on_disk, stub.url)["status"] == expected_status


def test_mark_selected_by_judge_overwrites_pending_and_persists(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/q2")
    store.is_seen(stub)
    store.mark_matched(stub)
    store.mark_selected_by_judge(stub)

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert _find_record(on_disk, stub.url)["status"] == "selected_by_judge"


def test_pending_entry_absent_from_disk_when_other_url_is_marked(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    pending_stub = StubLike(
        url="https://example.com/pending",
        company="Beta",
        title="Dev",
        location="Berlin",
    )
    real_stub = StubLike(url="https://example.com/real")
    store.is_seen(pending_stub)  # pending in memory, no disk write
    store.mark_matched(real_stub)  # triggers _persist

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert not _has_url(on_disk, pending_stub.url)
    assert _has_url(on_disk, real_stub.url)


# ---------------------------------------------------------------------------
# Post-enrich is_seen: pending URL with backfilled fields
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fuzzy-tuple tier
# ---------------------------------------------------------------------------


def test_fuzzy_hit_when_exact_tuple_misses_but_token_subset_matches(
    store_path: Path,
) -> None:
    """Seen: 'Senior Software Engineer Backend' (4 tokens) → new: 'Senior Software Engineer Backend Developer' should fuzzy-match."""
    a = StubLike(
        url="https://example.com/a",
        company="Acme",
        title="Senior Software Engineer Backend",
        location="Hamburg",
    )
    b = StubLike(
        url="https://example.com/b",
        company="Acme",
        title="Senior Software Engineer Backend Developer",
        location="Hamburg",
    )
    store = dedup_load(store_path)
    store.mark_out_of_domain(a)
    # exact tuple misses (different title_lc), fuzzy should match
    assert store.is_seen(b).kind == "fuzzy_hit"


def test_fuzzy_hit_direction_independent_longer_seen_first(
    store_path: Path,
) -> None:
    """Longer title seen first; shorter new title should still fuzzy-match."""
    a = StubLike(
        url="https://example.com/a",
        company="Acme",
        title="Senior Software Engineer Backend Developer",
        location="Hamburg",
    )
    b = StubLike(
        url="https://example.com/b",
        company="Acme",
        title="Senior Software Engineer Backend",
        location="Hamburg",
    )
    store = dedup_load(store_path)
    store.mark_out_of_domain(a)
    assert store.is_seen(b).kind == "fuzzy_hit"


def test_fuzzy_hit_does_not_fire_when_shorter_has_fewer_than_four_tokens(
    store_path: Path,
) -> None:
    """3-token shorter title must not trigger fuzzy tier."""
    a = StubLike(
        url="https://example.com/a",
        company="Acme",
        title="Software Engineer Backend",
        location="Hamburg",
    )
    b = StubLike(
        url="https://example.com/b",
        company="Acme",
        title="Software Engineer Backend Developer Extra",
        location="Hamburg",
    )
    store = dedup_load(store_path)
    store.mark_out_of_domain(a)
    # shorter ("Software Engineer Backend") has 3 tokens — must not fire
    assert store.is_seen(b).kind == "miss"


def test_fuzzy_hit_gender_markers_stripped_before_tokenization(
    store_path: Path,
) -> None:
    """Gender markers like (m/w/d) must be stripped so they don't affect token count or matching."""
    a = StubLike(
        url="https://example.com/a",
        company="Acme",
        title="Senior Software Engineer Backend (m/w/d)",
        location="Hamburg",
    )
    b = StubLike(
        url="https://example.com/b",
        company="Acme",
        title="Senior Software Engineer Backend Developer (w/m/d)",
        location="Hamburg",
    )
    store = dedup_load(store_path)
    store.mark_out_of_domain(a)
    # after stripping gender markers: 4 tokens ⊆ 5 tokens
    assert store.is_seen(b).kind == "fuzzy_hit"


def test_fuzzy_hit_writes_persistent_alias(store_path: Path) -> None:
    """Fuzzy hit on non-matched entry writes a persistent alias (same as tuple tier)."""
    a = StubLike(
        url="https://example.com/a",
        company="Acme",
        title="Senior Software Engineer Backend",
        location="Hamburg",
    )
    b = StubLike(
        url="https://example.com/b",
        company="Acme",
        title="Senior Software Engineer Backend Developer",
        location="Hamburg",
    )
    store = dedup_load(store_path)
    store.mark_out_of_domain(a)
    store.is_seen(b)  # fuzzy_hit — alias write should fire

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert _has_url(on_disk, b.url)
    record = _find_record(on_disk, b.url)
    assert record["status"] == "out_of_domain"
    assert a.url in record["urls"]


def test_fuzzy_hit_on_matched_returns_judge_pending(store_path: Path) -> None:
    """Fuzzy hit on a matched entry returns judge_pending (status-aware routing)."""
    a = StubLike(
        url="https://example.com/a",
        company="Acme",
        title="Senior Software Engineer Backend",
        location="Hamburg",
    )
    b = StubLike(
        url="https://example.com/b",
        company="Acme",
        title="Senior Software Engineer Backend Developer",
        location="Hamburg",
    )
    store = dedup_load(store_path)
    store.mark_matched(a)
    assert store.is_seen(b).kind == "judge_pending"


def test_pending_entry_populates_fuzzy_index_on_miss(store_path: Path) -> None:
    """On miss, pending entry is written and populates fuzzy index so a second call fuzzy-matches."""
    a = StubLike(
        url="https://example.com/a",
        company="Acme",
        title="Senior Software Engineer Backend",
        location="Hamburg",
    )
    b = StubLike(
        url="https://example.com/b",
        company="Acme",
        title="Senior Software Engineer Backend Developer",
        location="Hamburg",
    )
    store = dedup_load(store_path)
    assert store.is_seen(a).kind == "miss"  # writes pending, populates fuzzy index
    assert store.is_seen(b).kind == "fuzzy_hit"


def test_post_enrich_is_seen_catches_tuple_match_after_company_backfill(
    store: DeduplicationStore,
) -> None:
    """A pending URL with company=None that gets company backfilled hits an existing tuple."""
    existing = StubLike(url="https://example.com/existing")
    store.mark_out_of_domain(existing)

    # First call: company=None, no tuple index entry written for url_a
    url_a_pre = StubLike(url="https://example.com/a", company=None)
    assert store.is_seen(url_a_pre).kind == "miss"

    # Post-enrich: company now backfilled, same tuple as existing
    url_a_enriched = StubLike(url="https://example.com/a", company="Acme")
    result = store.is_seen(url_a_enriched)

    assert result.kind == "tuple_hit"


# ---------------------------------------------------------------------------
# Cooldown decay: selected_by_judge and expired
# ---------------------------------------------------------------------------


def test_tuple_hit_on_selected_by_judge_after_cooldown_returns_judge_pending(
    store_path: Path,
) -> None:
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/original"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "selected_by_judge",
                    "status_last_changed": "2020-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path, cooldown_days=30)
    b = StubLike(url="https://example.com/new-url")
    assert store.is_seen(b).kind == "judge_pending"


def test_tuple_hit_on_selected_by_judge_after_cooldown_updates_url_and_title(
    store_path: Path,
) -> None:
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/original"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "selected_by_judge",
                    "status_last_changed": "2020-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path, cooldown_days=30)
    b = StubLike(url="https://example.com/new-url")  # same tuple: acme/engineer/hamburg
    assert store.is_seen(b).kind == "judge_pending"
    # Any mark call flushes the full record dict to disk; use it to read back.
    store.mark_out_of_domain(StubLike(url="https://example.com/other", company="Other"))
    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    canonical_record = _find_record(on_disk, "https://example.com/original")
    assert canonical_record["urls"][0] == "https://example.com/new-url"


def test_cooldown_days_from_config_controls_decay_threshold(
    store_path: Path,
) -> None:
    # With cooldown_days=1, a record 2 days old is expired; same record within cooldown_days=30 is not.
    from datetime import timedelta

    two_days_ago = (date.today() - timedelta(days=2)).isoformat()
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/original"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "selected_by_judge",
                    "status_last_changed": two_days_ago,
                }
            }
        ),
        encoding="utf-8",
    )
    short_cooldown = dedup_load(store_path, cooldown_days=1)
    long_cooldown = dedup_load(store_path, cooldown_days=30)
    b = StubLike(url="https://example.com/new-url")
    assert short_cooldown.is_seen(b).kind == "judge_pending"
    assert long_cooldown.is_seen(b).kind == "tuple_hit"


def test_tuple_hit_on_selected_by_judge_within_cooldown_returns_tuple_hit(
    store_path: Path,
) -> None:
    store = dedup_load(store_path, cooldown_days=30)
    a = StubLike(url="https://example.com/original")
    store.mark_matched(a)
    store.mark_selected_by_judge(a)
    b = StubLike(url="https://example.com/new-url")
    assert store.is_seen(b).kind == "tuple_hit"


def test_tuple_hit_on_expired_within_cooldown_returns_tuple_hit(
    store_path: Path,
) -> None:
    store = dedup_load(store_path, cooldown_days=30)
    a = StubLike(url="https://example.com/original")
    store.mark_expired(a)
    b = StubLike(url="https://example.com/new-url")
    assert store.is_seen(b).kind == "tuple_hit"


def test_url_hit_on_selected_by_judge_within_cooldown_returns_url_hit(
    store_path: Path,
) -> None:
    store = dedup_load(store_path, cooldown_days=30)
    stub = StubLike(url="https://example.com/original")
    store.mark_matched(stub)
    store.mark_selected_by_judge(stub)
    assert store.is_seen(stub).kind == "url_hit"


def test_url_hit_on_expired_within_cooldown_returns_url_hit(
    store_path: Path,
) -> None:
    store = dedup_load(store_path, cooldown_days=30)
    stub = StubLike(url="https://example.com/original")
    store.mark_expired(stub)
    assert store.is_seen(stub).kind == "url_hit"


def test_url_hit_on_expired_after_cooldown_returns_miss(
    store_path: Path,
) -> None:
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/expired"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "expired",
                    "status_last_changed": "2020-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path, cooldown_days=30)
    assert store.is_seen(StubLike(url="https://example.com/expired")).kind == "miss"


def test_tuple_hit_on_expired_after_cooldown_returns_miss(
    store_path: Path,
) -> None:
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/original"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "expired",
                    "status_last_changed": "2020-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path, cooldown_days=30)
    b = StubLike(url="https://example.com/new-url")
    assert store.is_seen(b).kind == "miss"


def test_url_hit_on_selected_by_judge_after_cooldown_returns_judge_pending(
    store_path: Path,
) -> None:
    store_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": ["https://example.com/original"],
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "location_lc": "hamburg",
                    "status": "selected_by_judge",
                    "status_last_changed": "2020-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    store = dedup_load(store_path, cooldown_days=30)
    same = StubLike(url="https://example.com/original")
    assert store.is_seen(same).kind == "judge_pending"


# ---------------------------------------------------------------------------
# JSONL hit logging — tuple_hit and fuzzy_hit
# ---------------------------------------------------------------------------


def _read_dedup_events(logs_dir: Path) -> list[dict]:
    path = logs_dir / "pipeline" / "dedup.events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_tuple_hit_writes_event_to_jsonl(store_path: Path, tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    store = dedup_load(store_path, run_log=run_log)
    canonical = StubLike(url="https://example.com/original", title="Software Engineer")
    new_job = StubLike(url="https://example.com/new-url", title="software engineer")
    store.mark_out_of_domain(canonical)

    result = store.is_seen(new_job)

    assert result.kind == "tuple_hit"
    events = _read_dedup_events(logs_dir)
    assert len(events) == 1
    evt = events[0]
    assert evt["event"] == "tuple_hit"
    assert evt["new_url"] == new_job.url
    assert evt["canonical_url"] == canonical.url
    assert evt["new_title"] == new_job.title
    assert evt["canonical_title"] == "software engineer"


def test_fuzzy_hit_writes_event_to_jsonl(store_path: Path, tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    store = dedup_load(store_path, run_log=run_log)
    canonical = StubLike(
        url="https://example.com/canonical",
        title="Senior Software Engineer Backend",
    )
    new_job = StubLike(
        url="https://example.com/new-url",
        title="Senior Software Engineer Backend Developer",
    )
    store.mark_out_of_domain(canonical)

    result = store.is_seen(new_job)

    assert result.kind == "fuzzy_hit"
    events = _read_dedup_events(logs_dir)
    assert len(events) == 1
    evt = events[0]
    assert evt["event"] == "fuzzy_hit"
    assert evt["new_url"] == new_job.url
    assert evt["canonical_url"] == canonical.url
    assert evt["new_title"] == new_job.title
    assert evt["canonical_title"] == "senior software engineer backend"


def test_url_hit_does_not_write_to_jsonl(store_path: Path, tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    store = dedup_load(store_path, run_log=run_log)
    stub = StubLike(url="https://example.com/x")
    store.mark_out_of_domain(stub)

    result = store.is_seen(stub)

    assert result.kind == "url_hit"
    assert _read_dedup_events(logs_dir) == []


def test_run_hit_does_not_write_to_jsonl(store_path: Path, tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    store = dedup_load(store_path, run_log=run_log)
    stub = StubLike(url="https://example.com/x")

    with store.run_scope() as scope:
        scope.is_seen(stub)  # miss → registers in run
        result = scope.is_seen(stub)  # run_hit

    assert result.kind == "run_hit"
    assert _read_dedup_events(logs_dir) == []


def test_tuple_hit_concurrent_writes_produce_valid_jsonl(
    store_path: Path, tmp_path: Path
) -> None:
    """Multiple threads hitting is_seen simultaneously must not interleave JSONL lines."""
    from application_pipeline.parser_log import RunLog

    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    store = dedup_load(store_path, run_log=run_log)

    canonical = StubLike(url="https://example.com/canonical")
    store.mark_out_of_domain(canonical)

    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            new_key = StubLike(url=f"https://example.com/job-{i}")
            store.is_seen(new_key)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    events = _read_dedup_events(logs_dir)
    # All events must be valid JSON (no interleaved lines)
    assert all("event" in e for e in events)
    assert all(e["event"] == "tuple_hit" for e in events)
