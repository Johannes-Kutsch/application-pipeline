import json
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from application_pipeline.dedup import (
    RunScopedDedup,
    RunScopedSeenResult,
    load,
)


@dataclass
class Stub:
    url: str
    company: str | None = "Acme"
    title: str | None = "Engineer"
    location: str | None = "Hamburg"


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / ".seen.json"


@pytest.fixture
def store(store_path: Path):
    return load(store_path)


# --- Behavior 1: run_scope() yields RunScopedDedup; first is_seen returns miss ---


def test_run_scope_yields_run_scoped_dedup(store) -> None:
    with store.run_scope() as scope:
        assert isinstance(scope, RunScopedDedup)


def test_first_is_seen_on_unknown_url_returns_miss(store) -> None:
    with store.run_scope() as scope:
        result = scope.is_seen(Stub(url="https://example.com/new"))
        assert result == "miss"


# --- Behavior 2: second is_seen on same URL within scope returns run_hit ---


def test_second_is_seen_same_url_returns_run_hit(store) -> None:
    with store.run_scope() as scope:
        scope.is_seen(Stub(url="https://example.com/a"))
        result = scope.is_seen(Stub(url="https://example.com/a"))
        assert result == "run_hit"


# --- Behavior 3: run_hit does not mutate persistent store ---


def test_run_hit_does_not_mutate_persistent_store(store_path: Path, store) -> None:
    stub = Stub(url="https://example.com/a")
    with store.run_scope() as scope:
        scope.is_seen(stub)  # miss → adds to in-run set
        scope.is_seen(stub)  # run_hit → must not write to disk

    assert not store_path.exists()


# --- Behavior 4: persistent URL-tier hit returns url_hit ---


def test_persistent_url_hit_returns_url_hit(store) -> None:
    stub = Stub(url="https://example.com/kept")
    store.mark_kept(stub)
    with store.run_scope() as scope:
        result = scope.is_seen(stub)
        assert result == "url_hit"


# --- Behavior 5: persistent tuple-tier hit returns tuple_hit; alias lands in store ---


def test_persistent_tuple_hit_returns_tuple_hit(store) -> None:
    a = Stub(url="https://example.com/original")
    b = Stub(url="https://example.com/alias")
    store.mark_kept(a)
    with store.run_scope() as scope:
        result = scope.is_seen(b)
        assert result == "tuple_hit"


def test_tuple_hit_alias_write_lands_in_persistent_store(
    store_path: Path, store
) -> None:
    a = Stub(url="https://example.com/original")
    b = Stub(url="https://example.com/alias")
    store.mark_kept(a)
    with store.run_scope() as scope:
        scope.is_seen(b)

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert b.url in on_disk


def test_url_and_tuple_hits_do_not_populate_in_run_set(store) -> None:
    a = Stub(url="https://example.com/original")
    b = Stub(url="https://example.com/alias")
    store.mark_kept(a)
    with store.run_scope() as scope:
        scope.is_seen(a)  # url_hit
        scope.is_seen(b)  # tuple_hit
        # Neither should populate in-run set, so a third call with same URLs
        # should still return url_hit/tuple_hit — not run_hit
        assert scope.is_seen(a) == "url_hit"
        # b is now in persistent store as alias, so url_hit
        assert scope.is_seen(b) == "url_hit"


# --- Behavior 6: context exit clears in-run state; re-entering starts fresh ---


def test_re_entering_run_scope_starts_fresh(store) -> None:
    stub = Stub(url="https://example.com/fresh")
    with store.run_scope() as scope1:
        scope1.is_seen(stub)  # miss → adds to in-run set
        assert scope1.is_seen(stub) == "run_hit"

    with store.run_scope() as scope2:
        result = scope2.is_seen(stub)
        # persistent store was not mutated (no mark_*), so should be miss again
        assert result == "miss"


# --- Behavior 8: RunScopedSeenResult has exactly 4 variants ---


def test_run_scoped_seen_result_has_four_variants() -> None:
    from typing import get_args

    args = get_args(RunScopedSeenResult)
    assert set(args) == {"url_hit", "tuple_hit", "run_hit", "miss"}


# --- Behavior 9: thread safety of in-run set ---


def test_concurrent_is_seen_within_run_scope(store) -> None:
    n_threads = 10
    n_per_thread = 50
    errors: list[Exception] = []
    results: list[str] = []
    lock = threading.Lock()

    with store.run_scope() as scope:

        def worker(thread_id: int) -> None:
            try:
                for i in range(n_per_thread):
                    stub = Stub(url=f"https://example.com/t{thread_id}/i{i}")
                    r = scope.is_seen(stub)
                    with lock:
                        results.append(r)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert errors == [], f"threads raised: {errors}"
    assert all(r in {"miss", "run_hit", "url_hit", "tuple_hit"} for r in results)
