import json
import threading
from pathlib import Path

import pytest

from application_pipeline.extracts import (
    CardExtract,
    CardStore,
    ExtractStoreError,
    load_card_store,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "extracts.json"


@pytest.fixture
def store(store_path: Path) -> CardStore:
    return load_card_store(store_path)


def test_card_store_creates_parent_dir_on_first_write(tmp_path: Path) -> None:
    path = tmp_path / ".runtime-data" / "extracts.json"
    store = load_card_store(path)
    store.put(1, CardExtract(header="H", summary="S"))
    assert path.exists()
    assert path.parent.is_dir()


def test_get_on_unknown_key_returns_none(store: CardStore) -> None:
    assert store.get(0) is None


def test_put_then_get_round_trips_header_and_summary(store: CardStore) -> None:
    card = CardExtract(
        header="Role · Acme · Berlin", summary="Strong fit for the role."
    )
    store.put(42, card)
    result = store.get(42)
    assert result == card
    assert result.header == "Role · Acme · Berlin"
    assert result.summary == "Strong fit for the role."


def test_put_persists_to_disk(store_path: Path) -> None:
    store = load_card_store(store_path)
    store.put(1, CardExtract(header="Eng · Co · City", summary="Gut."))

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk["1"]["header"] == "Eng · Co · City"
    assert on_disk["1"]["summary"] == "Gut."


def test_card_extract_survives_reload(store_path: Path) -> None:
    card = CardExtract(header="Lead · Startup · Remote", summary="Ideal role.")
    load_card_store(store_path).put(1, card)

    reloaded = load_card_store(store_path)
    assert reloaded.get(1) == card


def test_delete_removes_extract(store: CardStore) -> None:
    store.put(1, CardExtract(header="H", summary="S"))
    store.delete(1)
    assert store.get(1) is None


def test_delete_unknown_key_is_silent_no_op(store_path: Path) -> None:
    store = load_card_store(store_path)
    store.delete(999)
    assert not store_path.exists()


def test_load_rejects_empty_file(store_path: Path) -> None:
    store_path.write_bytes(b"")
    with pytest.raises(ExtractStoreError):
        load_card_store(store_path)


def test_load_rejects_non_object_json(store_path: Path) -> None:
    store_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ExtractStoreError):
        load_card_store(store_path)


def test_url_keyed_extracts_raises_on_load(store_path: Path) -> None:
    store_path.write_text(
        json.dumps({"https://example.com/job": {"header": "H", "summary": "S"}}),
        encoding="utf-8",
    )
    with pytest.raises(ExtractStoreError, match="legacy URL-keyed format"):
        load_card_store(store_path)


def test_integer_keyed_extracts_loads_correctly(store_path: Path) -> None:
    existing = {"5": {"header": "H", "summary": "S"}}
    store_path.write_text(json.dumps(existing), encoding="utf-8")
    mtime_before = store_path.stat().st_mtime

    load_card_store(store_path)

    assert store_path.stat().st_mtime == mtime_before


def test_put_then_get_round_trips_body(store: CardStore) -> None:
    card = CardExtract(
        header="Role · Acme · Berlin",
        summary="Great fit.",
        body="Full raw description text.",
    )
    store.put(1, card)
    assert store.get(1) == card


def test_body_persists_to_disk(store_path: Path) -> None:
    store = load_card_store(store_path)
    store.put(1, CardExtract(header="H", summary="S", body="Raw body."))

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk["1"]["body"] == "Raw body."


def test_legacy_record_without_body_loads_with_empty_default(store_path: Path) -> None:
    store_path.write_text(
        json.dumps({"1": {"header": "H", "summary": "S"}}),
        encoding="utf-8",
    )
    card = load_card_store(store_path).get(1)
    assert card == CardExtract(header="H", summary="S", body="")


def test_replace_body_if_present_keeps_header_and_summary_and_noops_for_missing_key(
    store: CardStore,
) -> None:
    store.put(
        1,
        CardExtract(header="Persisted header", summary="Persisted summary", body="Old"),
    )

    assert store.replace_body_if_present(1, "Fresh raw description") is True
    assert store.get(1) == CardExtract(
        header="Persisted header",
        summary="Persisted summary",
        body="Fresh raw description",
    )
    assert store.replace_body_if_present(2, "Should not be stored") is False
    assert store.get(2) is None


def test_concurrent_writers_do_not_corrupt_file(store_path: Path) -> None:
    store = load_card_store(store_path)
    barrier = threading.Barrier(8)

    def worker(i: int) -> None:
        barrier.wait()
        store.put(i, CardExtract(header=f"H{i}", summary=f"S{i}"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert set(on_disk) == {str(i) for i in range(8)}
    for i in range(8):
        assert store.get(i) == CardExtract(header=f"H{i}", summary=f"S{i}")
