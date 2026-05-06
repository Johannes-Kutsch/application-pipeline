import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from application_pipeline import (
    DedupStoreError,
    DeduplicationStore,
    SeenStatus,
    load,
)
from application_pipeline.dedup import load as dedup_load


@dataclass
class StubLike:
    url: str
    company: str | None = "Acme"
    title: str | None = "Engineer"
    city: str | None = "Hamburg"


@dataclass
class PositionLike:
    url: str
    company: str | None = "Acme"
    title: str | None = "Engineer"
    city: str | None = "Hamburg"
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


def test_is_seen_false_on_fresh_store(store: DeduplicationStore) -> None:
    assert store.is_seen(StubLike(url="https://example.com/1")) is False


def test_mark_then_is_seen_returns_true(store: DeduplicationStore) -> None:
    stub = StubLike(url="https://example.com/1")
    store.mark_seen(stub, "kept")
    assert store.is_seen(stub) is True


def test_mark_seen_off_domain_persists_status(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/x")
    store.mark_seen(stub, "off_domain")

    assert store.is_seen(stub) is True

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    record = on_disk["https://example.com/x"]
    assert record["status"] == "off_domain"
    assert record["company_lc"] == "acme"
    assert record["title_lc"] == "engineer"
    assert record["city_lc"] == "hamburg"
    assert record["first_seen"] == date.today().isoformat()


def test_second_mark_same_url_is_silent_no_op(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/y")
    store.mark_seen(stub, "kept")
    first = json.loads(store_path.read_text(encoding="utf-8"))

    store.mark_seen(stub, "off_domain")
    second = json.loads(store_path.read_text(encoding="utf-8"))

    assert first == second
    assert second["https://example.com/y"]["status"] == "kept"


def test_first_seen_preserved_across_reload(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/keep")
    store.mark_seen(stub, "kept")

    reloaded = dedup_load(store_path)
    assert reloaded.is_seen(stub) is True

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk["https://example.com/keep"]["first_seen"] == date.today().isoformat()


def test_missing_file_initialises_empty(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.json"
    store = dedup_load(path)
    assert store.is_seen(StubLike(url="https://example.com/none")) is False


def test_zero_byte_file_initialises_empty(store_path: Path) -> None:
    store_path.write_bytes(b"")
    store = dedup_load(store_path)
    assert store.is_seen(StubLike(url="https://example.com/none")) is False


@pytest.mark.parametrize(
    "content",
    ["not-json", "[]", '"hello"', "123", '{"trailing":'],
)
def test_malformed_file_raises(store_path: Path, content: str) -> None:
    store_path.write_text(content, encoding="utf-8")
    with pytest.raises(DedupStoreError):
        dedup_load(store_path)


def test_os_replace_failure_propagates_unwrapped(
    store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/initial")
    store.mark_seen(stub, "kept")
    before = store_path.read_bytes()

    import os as _os

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_os, "replace", boom)

    with pytest.raises(OSError):
        store.mark_seen(StubLike(url="https://example.com/new"), "kept")

    assert store_path.read_bytes() == before


@pytest.mark.parametrize(
    "obj",
    [
        StubLike(url="https://example.com/a"),
        PositionLike(url="https://example.com/a"),
    ],
)
def test_mark_seen_accepts_stub_and_position(store_path: Path, obj: object) -> None:
    store = dedup_load(store_path)
    store.mark_seen(obj, "kept")  # type: ignore[arg-type]
    assert store.is_seen(obj) is True  # type: ignore[arg-type]


def test_debug_log_on_url_match(
    store_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/log")
    store.mark_seen(stub, "kept")

    with caplog.at_level(logging.DEBUG, logger="application_pipeline.dedup.store"):
        assert store.is_seen(stub) is True

    assert any("url" in r.getMessage().lower() for r in caplog.records)


def test_debug_log_on_mark_no_op(
    store_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/noop")
    store.mark_seen(stub, "kept")

    with caplog.at_level(logging.DEBUG, logger="application_pipeline.dedup.store"):
        store.mark_seen(stub, "off_domain")

    assert any("no-op" in r.getMessage().lower() for r in caplog.records)


def test_handles_none_company_title_city(store_path: Path) -> None:
    store = dedup_load(store_path)
    stub = StubLike(url="https://example.com/n", company=None, title=None, city=None)
    store.mark_seen(stub, "kept")

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    record = on_disk["https://example.com/n"]
    assert record["company_lc"] is None
    assert record["title_lc"] is None
    assert record["city_lc"] is None


def test_tuple_match_under_new_url_returns_true(store_path: Path) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://bundesagentur.de/job/1")
    b = StubLike(url="https://stellen.hamburg/job/42")
    store.mark_seen(a, "kept")
    assert store.is_seen(b) is True


def test_tuple_match_writes_alias_with_original_status_and_first_seen(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://bundesagentur.de/job/1")
    b = StubLike(url="https://stellen.hamburg/job/42")
    store.mark_seen(a, "kept")
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
                    "city_lc": "hamburg",
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
    store.mark_seen(a, "kept")
    store.is_seen(b)

    reloaded = dedup_load(store_path)
    # b.url is now in the URL dict; even if we changed the tuple fields, URL hits.
    different_tuple = StubLike(url=b.url, company="Other", title="Other", city="Other")
    assert reloaded.is_seen(different_tuple) is True


def test_tuple_match_case_insensitive(store_path: Path) -> None:
    store = dedup_load(store_path)
    store.mark_seen(
        StubLike(
            url="https://example.com/1",
            company="acme gmbh",
            title="engineer",
            city="hamburg",
        ),
        "kept",
    )
    assert (
        store.is_seen(
            StubLike(
                url="https://example.com/2",
                company="ACME GmbH",
                title="Engineer",
                city="Hamburg",
            )
        )
        is True
    )


def test_tuple_match_collapses_internal_whitespace(store_path: Path) -> None:
    store = dedup_load(store_path)
    store.mark_seen(
        StubLike(
            url="https://example.com/1",
            company="ACME GmbH",
            title="Software Engineer",
            city="Hamburg",
        ),
        "kept",
    )
    assert (
        store.is_seen(
            StubLike(
                url="https://example.com/2",
                company="ACME  GmbH",
                title="Software   Engineer",
                city=" Hamburg ",
            )
        )
        is True
    )


def test_tuple_lookup_skipped_when_field_none(store_path: Path) -> None:
    store = dedup_load(store_path)
    store.mark_seen(
        StubLike(
            url="https://example.com/1", company=None, title="Engineer", city="Hamburg"
        ),
        "kept",
    )
    # Different URL with same (None, title, city) must NOT match via tuple.
    assert (
        store.is_seen(
            StubLike(
                url="https://example.com/2",
                company=None,
                title="Engineer",
                city="Hamburg",
            )
        )
        is False
    )


def test_tuple_lookup_skipped_when_field_empty_after_normalize(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    store.mark_seen(
        StubLike(
            url="https://example.com/1", company="   ", title="Engineer", city="Hamburg"
        ),
        "kept",
    )
    assert (
        store.is_seen(
            StubLike(
                url="https://example.com/2",
                company="",
                title="Engineer",
                city="Hamburg",
            )
        )
        is False
    )


def test_none_company_url_match_still_works_on_second_is_seen(
    store_path: Path,
) -> None:
    store = dedup_load(store_path)
    stub = StubLike(
        url="https://example.com/n", company=None, title="Engineer", city="Hamburg"
    )
    store.mark_seen(stub, "kept")
    assert store.is_seen(stub) is True


def test_tuple_index_built_at_load_time(store_path: Path) -> None:
    store_path.write_text(
        json.dumps(
            {
                "https://example.com/seed": {
                    "company_lc": "acme",
                    "title_lc": "engineer",
                    "city_lc": "hamburg",
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
                city="Hamburg",
            )
        )
        is True
    )


def test_round_trip_mix_of_originals_and_aliases(store_path: Path) -> None:
    store = dedup_load(store_path)
    a = StubLike(url="https://a.example/1", company="Acme", title="Eng", city="HH")
    b = StubLike(url="https://b.example/1", company="Acme", title="Eng", city="HH")
    c = StubLike(url="https://a.example/2", company="Beta", title="PM", city="Berlin")
    store.mark_seen(a, "kept")
    store.is_seen(b)  # writes alias under b.url
    store.mark_seen(c, "off_domain")

    before = json.loads(store_path.read_text(encoding="utf-8"))
    reloaded = dedup_load(store_path)
    assert reloaded.is_seen(a) is True
    assert reloaded.is_seen(b) is True
    assert reloaded.is_seen(c) is True
    after = json.loads(store_path.read_text(encoding="utf-8"))
    assert before == after


def test_debug_log_on_tuple_match_with_alias_write(
    store_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = dedup_load(store_path)
    store.mark_seen(StubLike(url="https://a.example/1"), "kept")
    with caplog.at_level(logging.DEBUG, logger="application_pipeline.dedup.store"):
        store.is_seen(StubLike(url="https://b.example/1"))
    assert any("tuple" in r.getMessage().lower() for r in caplog.records)
