import json
import threading
from pathlib import Path

import pytest

from application_pipeline.extracts import ExtractStore, ExtractStoreError, load
from application_pipeline.llm.types import StructuredExtract


def _extract(**overrides: object) -> StructuredExtract:
    base: dict[str, object] = {
        "seniority": "senior",
        "work_model": "remote",
        "contract_type": "permanent",
        "key_skills": ["python", "rust"],
        "key_responsibilities": ["ship things"],
        "must_have_requirements": ["3+ years"],
        "notable_caveats": "Deutsch C1 required",
    }
    base.update(overrides)
    return StructuredExtract(**base)  # type: ignore[arg-type]


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "extracts.json"


@pytest.fixture
def store(store_path: Path) -> ExtractStore:
    return load(store_path)


def test_get_on_unknown_key_returns_none(store: ExtractStore) -> None:
    assert store.get("missing") is None


def test_put_then_get_round_trips_extract(store: ExtractStore) -> None:
    extract = _extract()
    store.put("k1", extract)
    assert store.get("k1") == extract


def test_delete_removes_extract(store: ExtractStore) -> None:
    store.put("k1", _extract())
    store.delete("k1")
    assert store.get("k1") is None


def test_delete_unknown_key_is_silent_no_op(store_path: Path) -> None:
    store = load(store_path)
    store.delete("never-put")
    assert not store_path.exists()


def test_put_persists_to_disk(store_path: Path) -> None:
    store = load(store_path)
    store.put("k1", _extract(seniority="junior"))

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk["k1"]["seniority"] == "junior"
    assert on_disk["k1"]["key_skills"] == ["python", "rust"]


def test_extract_survives_reload(store_path: Path) -> None:
    extract = _extract(notable_caveats="kein Homeoffice")
    load(store_path).put("k1", extract)

    reloaded = load(store_path)
    assert reloaded.get("k1") == extract


def test_overwrite_key_replaces_value(store_path: Path) -> None:
    store = load(store_path)
    store.put("k1", _extract(seniority="junior"))
    store.put("k1", _extract(seniority="staff"))

    assert store.get("k1") == _extract(seniority="staff")
    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk["k1"]["seniority"] == "staff"


def test_concurrent_writers_do_not_corrupt_file(store_path: Path) -> None:
    store = load(store_path)
    barrier = threading.Barrier(8)

    def worker(i: int) -> None:
        barrier.wait()
        store.put(f"k{i}", _extract(seniority=f"s{i}"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert set(on_disk) == {f"k{i}" for i in range(8)}
    for i in range(8):
        assert store.get(f"k{i}") == _extract(seniority=f"s{i}")


def test_load_rejects_empty_file(store_path: Path) -> None:
    store_path.write_bytes(b"")
    with pytest.raises(ExtractStoreError):
        load(store_path)


def test_load_rejects_invalid_json(store_path: Path) -> None:
    store_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ExtractStoreError):
        load(store_path)


def test_load_rejects_non_object_json(store_path: Path) -> None:
    store_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ExtractStoreError):
        load(store_path)
