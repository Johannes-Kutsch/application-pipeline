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
    return tmp_path / "extracts_v2.json"


@pytest.fixture
def store(store_path: Path) -> CardStore:
    return load_card_store(store_path)


def test_get_on_unknown_key_returns_none(store: CardStore) -> None:
    assert store.get("missing") is None


def test_put_then_get_round_trips_header_and_summary(store: CardStore) -> None:
    card = CardExtract(
        header="Role · Acme · Berlin", summary="Strong fit for the role."
    )
    store.put("abc123", card)
    result = store.get("abc123")
    assert result == card
    assert result.header == "Role · Acme · Berlin"
    assert result.summary == "Strong fit for the role."


def test_put_persists_to_disk(store_path: Path) -> None:
    store = load_card_store(store_path)
    store.put("k1", CardExtract(header="Eng · Co · City", summary="Gut."))

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk["k1"]["header"] == "Eng · Co · City"
    assert on_disk["k1"]["summary"] == "Gut."


def test_card_extract_survives_reload(store_path: Path) -> None:
    card = CardExtract(header="Lead · Startup · Remote", summary="Ideal role.")
    load_card_store(store_path).put("k1", card)

    reloaded = load_card_store(store_path)
    assert reloaded.get("k1") == card


def test_delete_removes_extract(store: CardStore) -> None:
    store.put("k1", CardExtract(header="H", summary="S"))
    store.delete("k1")
    assert store.get("k1") is None


def test_delete_unknown_key_is_silent_no_op(store_path: Path) -> None:
    store = load_card_store(store_path)
    store.delete("never-put")
    assert not store_path.exists()


def test_load_rejects_empty_file(store_path: Path) -> None:
    store_path.write_bytes(b"")
    with pytest.raises(ExtractStoreError):
        load_card_store(store_path)


def test_load_rejects_non_object_json(store_path: Path) -> None:
    store_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ExtractStoreError):
        load_card_store(store_path)


def test_concurrent_writers_do_not_corrupt_file(store_path: Path) -> None:
    store = load_card_store(store_path)
    barrier = threading.Barrier(8)

    def worker(i: int) -> None:
        barrier.wait()
        store.put(f"k{i}", CardExtract(header=f"H{i}", summary=f"S{i}"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert set(on_disk) == {f"k{i}" for i in range(8)}
    for i in range(8):
        assert store.get(f"k{i}") == CardExtract(header=f"H{i}", summary=f"S{i}")
