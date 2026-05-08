from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from application_pipeline.parsers import Parser, PositionStub
from application_pipeline.parsers.bundesagentur import BundesagenturParser, parser_class


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _queue(*responses: bytes) -> Iterator[bytes]:
    return iter(responses)


def _make_get(responses: list[bytes]) -> object:
    it = iter(responses)

    def http_get(url: str, timeout: float) -> bytes:
        return next(it)

    return http_get


# ---------------------------------------------------------------------------
# parser_class attribute
# ---------------------------------------------------------------------------


def test_parser_class_attribute_is_bundesagentur_parser() -> None:
    assert parser_class is BundesagenturParser


# ---------------------------------------------------------------------------
# Context manager / Parser protocol
# ---------------------------------------------------------------------------


def test_parser_satisfies_parser_protocol() -> None:
    p = BundesagenturParser(locations=["Hamburg"])
    assert isinstance(p, Parser)


def test_parser_is_usable_as_context_manager() -> None:
    with BundesagenturParser(locations=["Hamburg"]) as p:
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
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        stubs = list(p.discover("python"))
    assert len(stubs) == 2


def test_discover_stub_title_matches_api_titel() -> None:
    get = _make_get([_search_body([_item("x", "Data Scientist")]), _search_body([])])
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        (stub,) = list(p.discover("python"))
    assert stub.title == "Data Scientist"


def test_discover_stub_source_is_bundesagentur() -> None:
    get = _make_get([_search_body([_item()]), _search_body([])])
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        (stub,) = list(p.discover("python"))
    assert stub.source == "bundesagentur"


def test_discover_stub_language_is_de() -> None:
    get = _make_get([_search_body([_item()]), _search_body([])])
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        (stub,) = list(p.discover("python"))
    assert stub.language == "de"


def test_discover_stub_company_from_arbeitgeber() -> None:
    get = _make_get([_search_body([_item(company="Muster GmbH")]), _search_body([])])
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        (stub,) = list(p.discover("python"))
    assert stub.company == "Muster GmbH"


def test_discover_stub_location_from_arbeitsort_ort() -> None:
    get = _make_get([_search_body([_item(city="Berlin")]), _search_body([])])
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        (stub,) = list(p.discover("python"))
    assert stub.location == "Berlin"


def test_discover_stub_url_contains_hash_id() -> None:
    get = _make_get([_search_body([_item("myhash")]), _search_body([])])
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        (stub,) = list(p.discover("python"))
    assert "myhash" in stub.url


def test_discover_stub_company_none_when_arbeitgeber_absent() -> None:
    get = _make_get([_search_body([_item(company=None)]), _search_body([])])
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        (stub,) = list(p.discover("python"))
    assert stub.company is None


def test_discover_stub_location_none_when_arbeitsort_absent() -> None:
    get = _make_get([_search_body([_item(city=None)]), _search_body([])])
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        (stub,) = list(p.discover("python"))
    assert stub.location is None


# ---------------------------------------------------------------------------
# discover — pagination
# ---------------------------------------------------------------------------


def test_discover_paginates_until_empty_page() -> None:
    page0 = _search_body([_item("id1"), _item("id2")])
    page1 = _search_body([_item("id3")])
    page2 = _search_body([])
    get = _make_get([page0, page1, page2])
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        stubs = list(p.discover("python"))
    assert len(stubs) == 3


def test_discover_stops_on_null_stellenangebote() -> None:
    body = json.dumps({"maxErgebnisse": 0, "stellenangebote": None}).encode()
    get = _make_get([body])
    with BundesagenturParser(locations=["Hamburg"], _http_get=get) as p:
        stubs = list(p.discover("python"))
    assert stubs == []


# ---------------------------------------------------------------------------
# discover — locations and remote
# ---------------------------------------------------------------------------


def test_discover_queries_each_location() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(
        locations=["Hamburg", "Berlin"], _http_get=capturing_get
    ) as p:
        list(p.discover("python"))

    assert any("Hamburg" in u for u in urls)
    assert any("Berlin" in u for u in urls)


def test_discover_includes_bundesweit_when_include_remote() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(
        locations=["Hamburg"], include_remote=True, _http_get=capturing_get
    ) as p:
        list(p.discover("python"))

    assert any("bundesweit" in u.lower() for u in urls)


def test_discover_does_not_include_bundesweit_when_flag_false() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(
        locations=["Hamburg"], include_remote=False, _http_get=capturing_get
    ) as p:
        list(p.discover("python"))

    assert not any("bundesweit" in u.lower() for u in urls)


# ---------------------------------------------------------------------------
# discover — deduplication across locations
# ---------------------------------------------------------------------------


def test_discover_deduplicates_same_hash_across_locations() -> None:
    shared = _item("same_hash", "Dev")
    get = _make_get(
        [
            _search_body([shared]),
            _search_body([]),
            _search_body([shared]),
            _search_body([]),
        ]
    )
    with BundesagenturParser(locations=["Hamburg", "Berlin"], _http_get=get) as p:
        stubs = list(p.discover("python"))
    assert len(stubs) == 1


# ---------------------------------------------------------------------------
# discover — error handling
# ---------------------------------------------------------------------------


def test_discover_raises_parser_error_on_http_failure() -> None:
    from application_pipeline.parsers import ParserError

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with BundesagenturParser(
        locations=["Hamburg"], _http_get=failing_get, _retries=1
    ) as p:
        with pytest.raises(ParserError):
            list(p.discover("python"))


# ---------------------------------------------------------------------------
# enrich — raw description
# ---------------------------------------------------------------------------


def test_enrich_returns_position_with_raw_description() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body(description="We are hiring.")])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description == "We are hiring."


def test_enrich_strips_html_tags_from_description() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body(description="<p>Hello</p><p>World</p>")])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert "<p>" not in pos.raw_description
    assert "Hello" in pos.raw_description
    assert "World" in pos.raw_description


def test_enrich_decodes_html_entities_in_description() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body(description="Geh&auml;lter &amp; Benefits")])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert "Gehälter" in pos.raw_description
    assert "&amp;" not in pos.raw_description


def test_enrich_empty_description_when_field_absent() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    body = json.dumps({"hashId": "abc", "titel": "Dev"}).encode()
    get = _make_get([body])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description == ""


# ---------------------------------------------------------------------------
# enrich — contract_type
# ---------------------------------------------------------------------------


def test_enrich_maps_befristung_1_to_permanent() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body(befristung=1)])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.contract_type == "permanent"


def test_enrich_maps_befristung_2_to_fixed_term() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body(befristung=2)])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.contract_type == "fixed-term"


def test_enrich_contract_type_none_when_befristung_absent() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body()])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.contract_type is None


# ---------------------------------------------------------------------------
# enrich — employment_type
# ---------------------------------------------------------------------------


def test_enrich_maps_vz_to_full_time() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body(arbeitszeitModelle=["vz"])])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.employment_type == "full-time"


def test_enrich_maps_tz_to_part_time() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body(arbeitszeitModelle=["tz"])])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.employment_type == "part-time"


def test_enrich_employment_type_none_when_arbeitszeitmodelle_absent() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body()])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.employment_type is None


# ---------------------------------------------------------------------------
# enrich — dates
# ---------------------------------------------------------------------------


def test_enrich_parses_posted_date() -> None:
    from datetime import date

    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body(aktuelleVeroeffentlichungsdatum="2024-03-15")])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.posted_date == date(2024, 3, 15)


def test_enrich_parses_deadline() -> None:
    from datetime import date

    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body(bewerbungsschluss="2024-04-30")])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.deadline == date(2024, 4, 30)


def test_enrich_posted_date_none_when_field_absent() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body()])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.posted_date is None


# ---------------------------------------------------------------------------
# enrich — error handling
# ---------------------------------------------------------------------------


def test_enrich_raises_parser_error_on_http_failure() -> None:
    from application_pipeline.parsers import ParserError

    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with BundesagenturParser(locations=[], _http_get=failing_get, _retries=1) as p:
        with pytest.raises(ParserError):
            p.enrich(stub)


# ---------------------------------------------------------------------------
# enrich — stub reference preserved
# ---------------------------------------------------------------------------


def test_enrich_position_references_original_stub() -> None:
    stub = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="bundesagentur",
    )
    get = _make_get([_detail_body()])
    with BundesagenturParser(locations=[], _http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.stub is stub
