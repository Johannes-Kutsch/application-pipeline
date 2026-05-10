from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pytest

from application_pipeline.parsers import Parser, ParserQuery, PositionStub
from application_pipeline.parsers.bundesagentur_api import (
    BundesagenturParser,
    parser_class,
)
from application_pipeline.parsers.http import HttpGet

_FIXTURES = Path(__file__).parent / "fixtures" / "bundesagentur"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _search_body(items: list[dict], total: int | None = None) -> bytes:
    return json.dumps(
        {
            "maxErgebnisse": total if total is not None else len(items),
            "stellenangebote": items,
        }
    ).encode()


def _detail_body(
    hash_id: str = "abc123",
    title: str = "Software Engineer",
    description: str = "",
    **extra: object,
) -> bytes:
    return json.dumps(
        {
            "hashId": hash_id,
            "titel": title,
            "stellenbeschreibung": description,
            **extra,
        }
    ).encode()


def _item(
    hash_id: str = "abc123",
    title: str = "Software Engineer",
    company: str | None = "Acme GmbH",
    city: str | None = "Hamburg",
) -> dict:
    result: dict = {
        "hashId": hash_id,
        "titel": title,
        "aktuelleVeroeffentlichungsdatum": "2024-01-15",
    }
    if company is not None:
        result["arbeitgeber"] = company
    if city is not None:
        result["arbeitsort"] = {"ort": city, "plz": "20000"}
    return result


def _make_get(responses: list[bytes]) -> HttpGet:
    it = iter(responses)

    def http_get(url: str, timeout: float) -> bytes:
        return next(it)

    return http_get


def _query(**kwargs: object) -> ParserQuery:
    defaults: dict = {"keyword": "python", "location": "Hamburg", "max_results": 100}
    defaults.update(kwargs)
    return ParserQuery(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def stub() -> PositionStub:
    return PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="Bundesagentur",
    )


# ---------------------------------------------------------------------------
# parser_class attribute
# ---------------------------------------------------------------------------


def test_parser_class_attribute_is_bundesagentur_parser() -> None:
    assert parser_class is BundesagenturParser


# ---------------------------------------------------------------------------
# Context manager / Parser protocol
# ---------------------------------------------------------------------------


def test_parser_satisfies_parser_protocol() -> None:
    p = BundesagenturParser()
    assert isinstance(p, Parser)


def test_parser_is_usable_as_context_manager() -> None:
    with BundesagenturParser() as p:
        assert isinstance(p, BundesagenturParser)


# ---------------------------------------------------------------------------
# discover — basic stub fields
# ---------------------------------------------------------------------------


def test_discover_yields_one_stub_per_result() -> None:
    get = _make_get(
        [
            _search_body([_item("id1", "Dev A"), _item("id2", "Dev B")]),
            _search_body([]),
        ]
    )
    with BundesagenturParser(_http_get=get) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 2


def test_discover_stub_title_matches_api_titel() -> None:
    get = _make_get([_search_body([_item("x", "Data Scientist")]), _search_body([])])
    with BundesagenturParser(_http_get=get) as p:
        (stub,) = list(p.discover(_query()))
    assert stub.title == "Data Scientist"


def test_discover_stub_source_is_display_name() -> None:
    get = _make_get([_search_body([_item()]), _search_body([])])
    with BundesagenturParser(_http_get=get) as p:
        (stub,) = list(p.discover(_query()))
    assert stub.source == "Bundesagentur"


def test_discover_stub_language_is_de() -> None:
    get = _make_get([_search_body([_item()]), _search_body([])])
    with BundesagenturParser(_http_get=get) as p:
        (stub,) = list(p.discover(_query()))
    assert stub.language == "de"


def test_discover_stub_company_from_arbeitgeber() -> None:
    get = _make_get([_search_body([_item(company="Muster GmbH")]), _search_body([])])
    with BundesagenturParser(_http_get=get) as p:
        (stub,) = list(p.discover(_query()))
    assert stub.company == "Muster GmbH"


def test_discover_stub_location_from_arbeitsort_ort() -> None:
    get = _make_get([_search_body([_item(city="Berlin")]), _search_body([])])
    with BundesagenturParser(_http_get=get) as p:
        (stub,) = list(p.discover(_query()))
    assert stub.location == "Berlin"


def test_discover_stub_url_contains_hash_id() -> None:
    get = _make_get([_search_body([_item("myhash")]), _search_body([])])
    with BundesagenturParser(_http_get=get) as p:
        (stub,) = list(p.discover(_query()))
    assert "myhash" in stub.url


def test_discover_stub_company_none_when_arbeitgeber_absent() -> None:
    get = _make_get([_search_body([_item(company=None)]), _search_body([])])
    with BundesagenturParser(_http_get=get) as p:
        (stub,) = list(p.discover(_query()))
    assert stub.company is None


def test_discover_stub_location_none_when_arbeitsort_absent() -> None:
    get = _make_get([_search_body([_item(city=None)]), _search_body([])])
    with BundesagenturParser(_http_get=get) as p:
        (stub,) = list(p.discover(_query()))
    assert stub.location is None


# ---------------------------------------------------------------------------
# discover — fixture-based
# ---------------------------------------------------------------------------


def test_discover_parses_search_fixture() -> None:
    search = _load("search.json")
    get = _make_get([search, _search_body([])])
    with BundesagenturParser(_http_get=get) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 2
    assert stubs[0].title == "Software Engineer"
    assert stubs[0].source == "Bundesagentur"
    assert stubs[1].title == "Data Scientist"


# ---------------------------------------------------------------------------
# discover — pagination
# ---------------------------------------------------------------------------


def test_discover_paginates_until_empty_page() -> None:
    page0 = _search_body([_item("id1"), _item("id2")])
    page1 = _search_body([_item("id3")])
    page2 = _search_body([])
    get = _make_get([page0, page1, page2])
    with BundesagenturParser(_http_get=get) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 3


def test_discover_stops_on_null_stellenangebote() -> None:
    body = json.dumps({"maxErgebnisse": 0, "stellenangebote": None}).encode()
    get = _make_get([body])
    with BundesagenturParser(_http_get=get) as p:
        stubs = list(p.discover(_query()))
    assert stubs == []


# ---------------------------------------------------------------------------
# discover — location slug resolution
# ---------------------------------------------------------------------------


def test_discover_resolves_location_to_slug_in_url() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(_http_get=capturing_get) as p:
        list(p.discover(_query(location="Hamburg")))

    assert any("wo=Hamburg" in u for u in urls)


def test_discover_normalizes_location_before_slug_lookup() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(_http_get=capturing_get) as p:
        list(p.discover(_query(location="  MÜNCHEN  ")))

    assert any("M%C3%BCnchen" in u or "München" in u for u in urls)


def test_discover_unknown_location_yields_nothing_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def capturing_get(url: str, timeout: float) -> bytes:
        return _search_body([])

    with caplog.at_level(logging.WARNING):
        with BundesagenturParser(_http_get=capturing_get) as p:
            stubs = list(p.discover(_query(location="unknown_city_xyz")))

    assert stubs == []
    assert any("unmapped_location" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# discover — remote (location=None uses arbeitszeit=ho)
# ---------------------------------------------------------------------------


def test_discover_location_none_uses_arbeitszeit_ho() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(_http_get=capturing_get) as p:
        list(p.discover(_query(location=None)))

    assert any("arbeitszeit=ho" in u for u in urls)


def test_discover_location_none_omits_wo_param() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(_http_get=capturing_get) as p:
        list(p.discover(_query(location=None)))

    assert all("wo=" not in u for u in urls)


# ---------------------------------------------------------------------------
# discover — max_results
# ---------------------------------------------------------------------------


def test_discover_respects_max_results() -> None:
    items = [_item(f"id{i}") for i in range(10)]
    get = _make_get([_search_body(items)])
    with BundesagenturParser(_http_get=get) as p:
        stubs = list(p.discover(_query(max_results=3)))
    assert len(stubs) == 3


# ---------------------------------------------------------------------------
# discover — deduplication
# ---------------------------------------------------------------------------


def test_discover_deduplicates_same_hash_id() -> None:
    shared = _item("same_hash", "Dev")
    page0 = _search_body([shared, shared])
    get = _make_get([page0, _search_body([])])
    with BundesagenturParser(_http_get=get) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 1


# ---------------------------------------------------------------------------
# discover — error handling
# ---------------------------------------------------------------------------


def test_discover_raises_parser_error_on_http_failure() -> None:
    from application_pipeline.parsers import ParserError

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with BundesagenturParser(_http_get=failing_get, _retries=1) as p:
        with pytest.raises(ParserError):
            list(p.discover(_query()))


# ---------------------------------------------------------------------------
# enrich — fixture-based
# ---------------------------------------------------------------------------


def test_enrich_parses_detail_fixture(stub: PositionStub) -> None:
    get = _make_get([_load("detail.json")])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "Software Engineer" in pos.raw_description or pos.raw_description != ""
    assert pos.contract_type == "permanent"
    assert pos.employment_type == "full-time"
    assert pos.posted_date == date(2024, 1, 15)


# ---------------------------------------------------------------------------
# enrich — raw description
# ---------------------------------------------------------------------------


def test_enrich_returns_position_with_raw_description(stub: PositionStub) -> None:
    get = _make_get([_detail_body(description="We are hiring.")])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description == "We are hiring."


def test_enrich_strips_html_tags_from_description(stub: PositionStub) -> None:
    get = _make_get([_detail_body(description="<p>Hello</p><p>World</p>")])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "<p>" not in pos.raw_description
    assert "Hello" in pos.raw_description
    assert "World" in pos.raw_description


def test_enrich_decodes_html_entities_in_description(stub: PositionStub) -> None:
    get = _make_get([_detail_body(description="Geh&auml;lter &amp; Benefits")])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "Gehälter" in pos.raw_description
    assert "&amp;" not in pos.raw_description


def test_enrich_empty_description_when_field_absent(stub: PositionStub) -> None:
    body = json.dumps({"hashId": "abc", "titel": "Dev"}).encode()
    get = _make_get([body])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description == ""


# ---------------------------------------------------------------------------
# enrich — contract_type
# ---------------------------------------------------------------------------


def test_enrich_maps_befristung_1_to_permanent(stub: PositionStub) -> None:
    get = _make_get([_detail_body(befristung=1)])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.contract_type == "permanent"


def test_enrich_maps_befristung_2_to_fixed_term(stub: PositionStub) -> None:
    get = _make_get([_detail_body(befristung=2)])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.contract_type == "fixed-term"


def test_enrich_contract_type_none_when_befristung_absent(stub: PositionStub) -> None:
    get = _make_get([_detail_body()])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.contract_type is None


# ---------------------------------------------------------------------------
# enrich — employment_type
# ---------------------------------------------------------------------------


def test_enrich_maps_vz_to_full_time(stub: PositionStub) -> None:
    get = _make_get([_detail_body(arbeitszeitModelle=["vz"])])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.employment_type == "full-time"


def test_enrich_maps_tz_to_part_time(stub: PositionStub) -> None:
    get = _make_get([_detail_body(arbeitszeitModelle=["tz"])])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.employment_type == "part-time"


def test_enrich_employment_type_none_when_arbeitszeitmodelle_absent(
    stub: PositionStub,
) -> None:
    get = _make_get([_detail_body()])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.employment_type is None


# ---------------------------------------------------------------------------
# enrich — dates
# ---------------------------------------------------------------------------


def test_enrich_parses_posted_date(stub: PositionStub) -> None:
    get = _make_get([_detail_body(aktuelleVeroeffentlichungsdatum="2024-03-15")])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.posted_date == date(2024, 3, 15)


def test_enrich_parses_deadline(stub: PositionStub) -> None:
    get = _make_get([_detail_body(bewerbungsschluss="2024-04-30")])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.deadline == date(2024, 4, 30)


def test_enrich_posted_date_none_when_field_absent(stub: PositionStub) -> None:
    get = _make_get([_detail_body()])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.posted_date is None


# ---------------------------------------------------------------------------
# enrich — error handling
# ---------------------------------------------------------------------------


def test_enrich_raises_parser_error_on_http_failure() -> None:
    from application_pipeline.parsers import ParserError

    s = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="Bundesagentur",
    )

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with BundesagenturParser(_http_get=failing_get, _retries=1) as p:
        with pytest.raises(ParserError):
            p.enrich(s)


# ---------------------------------------------------------------------------
# enrich — stub reference preserved
# ---------------------------------------------------------------------------


def test_enrich_position_references_original_stub(stub: PositionStub) -> None:
    get = _make_get([_detail_body()])
    with BundesagenturParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.stub is stub
