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
    assert result is not card
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


def test_load_card_store_wipes_retired_v1_integer_keyed_records(
    store_path: Path,
) -> None:
    store_path.write_text(
        json.dumps(
            {
                "5": {
                    "seniority": "senior",
                    "work_model": "remote",
                    "contract_type": "permanent",
                    "key_skills": ["Python"],
                    "key_responsibilities": ["Build systems"],
                    "must_have_requirements": ["Distributed systems"],
                    "notable_caveats": "",
                }
            }
        ),
        encoding="utf-8",
    )

    store = load_card_store(store_path)

    assert store.get(5) is None
    assert json.loads(store_path.read_text(encoding="utf-8")) == {}


def test_load_rejects_card_like_integer_keyed_record_with_non_string_header_or_summary(
    store_path: Path,
) -> None:
    original = json.dumps({"5": {"header": 7, "summary": "S"}})
    store_path.write_text(original, encoding="utf-8")

    with pytest.raises(ExtractStoreError, match="invalid card record"):
        load_card_store(store_path)

    assert store_path.read_text(encoding="utf-8") == original


def test_load_rejects_record_outside_documented_card_store_shapes(
    store_path: Path,
) -> None:
    original = json.dumps({"5": {"header": "H", "body": "B"}})
    store_path.write_text(original, encoding="utf-8")

    with pytest.raises(ExtractStoreError, match="invalid card record"):
        load_card_store(store_path)

    assert store_path.read_text(encoding="utf-8") == original


def test_load_rejects_current_card_record_with_non_string_body(
    store_path: Path,
) -> None:
    original = json.dumps({"5": {"header": "H", "summary": "S", "body": 99}})
    store_path.write_text(original, encoding="utf-8")

    with pytest.raises(ExtractStoreError, match="invalid card record"):
        load_card_store(store_path)

    assert store_path.read_text(encoding="utf-8") == original


def test_load_rejects_integer_keyed_record_that_is_neither_card_nor_retired_v1(
    store_path: Path,
) -> None:
    original = json.dumps({"5": ["not", "a", "record"]})
    store_path.write_text(original, encoding="utf-8")

    with pytest.raises(ExtractStoreError, match="invalid card record"):
        load_card_store(store_path)

    assert store_path.read_text(encoding="utf-8") == original


def test_load_rejects_mixed_current_card_and_retired_v1_integer_keyed_records(
    store_path: Path,
) -> None:
    original = json.dumps(
        {
            "5": {"header": "H", "summary": "S"},
            "7": {"company": "Acme", "title": "Legacy extract"},
        }
    )
    store_path.write_text(original, encoding="utf-8")

    with pytest.raises(
        ExtractStoreError, match="mixes current card records with retired v1 records"
    ):
        load_card_store(store_path)

    assert store_path.read_text(encoding="utf-8") == original


def test_url_keyed_extracts_raises_on_load(store_path: Path) -> None:
    store_path.write_text(
        json.dumps({"https://example.com/job": {"header": "H", "summary": "S"}}),
        encoding="utf-8",
    )
    with pytest.raises(ExtractStoreError, match="legacy URL-keyed format"):
        load_card_store(store_path)


def test_load_rejects_url_keyed_legacy_data_even_when_not_first_top_level_key(
    store_path: Path,
) -> None:
    original = json.dumps(
        {
            "5": {"header": "H", "summary": "S"},
            "https://example.com/job": {"header": "Legacy", "summary": "Entry"},
        }
    )
    store_path.write_text(original, encoding="utf-8")

    with pytest.raises(ExtractStoreError, match="legacy URL-keyed format"):
        load_card_store(store_path)

    assert store_path.read_text(encoding="utf-8") == original


def test_load_rejects_url_keyed_legacy_data_before_any_retired_v1_wipe(
    store_path: Path,
) -> None:
    original = json.dumps(
        {
            "5": {"company": "Acme", "title": "Legacy extract"},
            "https://example.com/job": {"header": "Legacy", "summary": "Entry"},
        }
    )
    store_path.write_text(original, encoding="utf-8")

    with pytest.raises(ExtractStoreError, match="legacy URL-keyed format"):
        load_card_store(store_path)

    assert store_path.read_text(encoding="utf-8") == original


def test_load_card_store_surfaces_persisted_card_through_get(store_path: Path) -> None:
    existing = {"5": {"header": "H", "summary": "S", "body": "B"}}
    store_path.write_text(json.dumps(existing), encoding="utf-8")

    store = load_card_store(store_path)

    assert store.get(5) == CardExtract(header="H", summary="S", body="B")


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


def test_load_card_store_does_not_rewrite_bodyless_persisted_card_shape(
    store_path: Path,
) -> None:
    existing = {"5": {"header": "H", "summary": "S"}}
    store_path.write_text(json.dumps(existing), encoding="utf-8")
    mtime_before = store_path.stat().st_mtime

    store = load_card_store(store_path)

    assert store.get(5) == CardExtract(header="H", summary="S", body="")
    assert store_path.stat().st_mtime == mtime_before
    assert json.loads(store_path.read_text(encoding="utf-8")) == existing


def test_get_returns_equal_distinct_values_on_repeated_reads(store: CardStore) -> None:
    store.put(1, CardExtract(header="H", summary="S", body="B"))

    first = store.get(1)
    second = store.get(1)

    assert first == CardExtract(header="H", summary="S", body="B")
    assert second == first
    assert second is not first


def test_loaded_legacy_record_without_body_still_allows_body_replacement(
    store_path: Path,
) -> None:
    store_path.write_text(
        json.dumps(
            {"1": {"header": "Persisted header", "summary": "Persisted summary"}}
        ),
        encoding="utf-8",
    )
    store = load_card_store(store_path)

    assert store.replace_body_if_present(1, "Fresh raw description") is True
    assert store.get(1) == CardExtract(
        header="Persisted header",
        summary="Persisted summary",
        body="Fresh raw description",
    )


def test_replace_body_if_present_persists_updated_card_shape(store_path: Path) -> None:
    store = load_card_store(store_path)
    store.put(7, CardExtract(header="Persisted header", summary="Persisted summary"))

    assert store.replace_body_if_present(7, "Fresh raw description") is True

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk == {
        "7": {
            "header": "Persisted header",
            "summary": "Persisted summary",
            "body": "Fresh raw description",
        }
    }


def test_replace_body_if_present_keeps_header_and_summary_and_noops_for_empty_or_missing_input(
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
    assert store.replace_body_if_present(1, "") is False
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


def test_delete_persists_remaining_integer_keyed_cards(store_path: Path) -> None:
    store = load_card_store(store_path)
    store.put(9, CardExtract(header="Header 9", summary="Summary 9", body="Body 9"))
    store.put(11, CardExtract(header="Header 11", summary="Summary 11"))

    store.delete(9)

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert on_disk == {
        "11": {
            "header": "Header 11",
            "summary": "Summary 11",
            "body": "",
        }
    }
