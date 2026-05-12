from __future__ import annotations

import json
from datetime import date
from typing import Callable

import pytest

from application_pipeline.parsers import Parser, ParserQuery, PositionStub
from application_pipeline.parsers.types import City
from application_pipeline.parsers.http import HttpGet
from application_pipeline.parsers.stellen_hamburg_api import (
    StellenHamburgParser,
    parser_class,
)

HttpPost = Callable[[str, bytes, float], bytes]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _search_body(items: list[dict], total: int | None = None) -> bytes:
    return json.dumps(
        {
            "LanguageCode": "DE",
            "SearchResult": {
                "SearchResultCount": len(items),
                "SearchResultCountAll": total if total is not None else len(items),
                "SearchResultItems": items,
                "UserArea": {"ExecutionError": 0},
            },
        }
    ).encode()


def _item(
    obj_id: str = "39581",
    title: str = "Software Engineer",
    company: str | None = "Behörde für Stadtentwicklung",
    location: str | None = "Hamburg",
) -> dict:
    result: dict = {
        "MatchedObjectId": obj_id,
        "MatchedObjectDescriptor": {
            "ID": obj_id,
            "PositionID": f"j{obj_id.zfill(9)}",
            "PositionTitle": title,
            "PositionURI": f"https://stellen.hamburg.de/index.php?ac=jobad&id={obj_id}",
        },
        "RelevanceScore": 0,
        "RelevanceRank": 1,
    }
    if company is not None:
        result["MatchedObjectDescriptor"]["OrganizationName"] = company
    if location is not None:
        result["MatchedObjectDescriptor"]["PositionLocation"] = [
            {"CountrySubDivisionName": location, "CountryCode": "DE"}
        ]
    return result


def _detail_html(
    description: str = "",
    date_posted: str = "2026-01-20",
    valid_through: str | None = None,
    employment_type: str | None = None,
) -> bytes:
    ld_json: dict = {
        "@context": "http://schema.org/",
        "@type": "JobPosting",
        "title": "Software Engineer",
        "description": description,
        "datePosted": date_posted,
    }
    if valid_through is not None:
        ld_json["validThrough"] = valid_through
    if employment_type is not None:
        ld_json["employmentType"] = employment_type

    script = f'<script type="application/ld+json">{json.dumps(ld_json)}</script>'
    return f"<html><head>{script}</head><body></body></html>".encode()


def _make_post(responses: list[bytes]) -> HttpPost:
    it = iter(responses)

    def http_post(url: str, body: bytes, timeout: float) -> bytes:
        return next(it)

    return http_post


def _make_get(responses: list[bytes]) -> HttpGet:
    it = iter(responses)

    def http_get(url: str, timeout: float) -> bytes:
        return next(it)

    return http_get


def _query(**kwargs: object) -> ParserQuery:
    defaults: dict = {
        "keyword": "python",
        "location": City("hamburg"),
        "max_results": 100,
    }
    defaults.update(kwargs)
    return ParserQuery(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def stub() -> PositionStub:
    return PositionStub(
        url="https://stellen.hamburg.de/index.php?ac=jobad&id=39581",
        title="Software Engineer",
        source="stellen_hamburg",
    )


# ---------------------------------------------------------------------------
# parser_class attribute
# ---------------------------------------------------------------------------


def test_parser_class_attribute_is_stellen_hamburg_parser() -> None:
    assert parser_class is StellenHamburgParser


# ---------------------------------------------------------------------------
# Context manager / Parser protocol
# ---------------------------------------------------------------------------


def test_parser_satisfies_parser_protocol() -> None:
    p = StellenHamburgParser()
    assert isinstance(p, Parser)


def test_parser_is_usable_as_context_manager() -> None:
    with StellenHamburgParser() as p:
        assert isinstance(p, StellenHamburgParser)


# ---------------------------------------------------------------------------
# discover — basic stub fields
# ---------------------------------------------------------------------------


def test_discover_yields_one_stub_per_result() -> None:
    post = _make_post(
        [
            _search_body([_item("1", "Dev A"), _item("2", "Dev B")]),
        ]
    )
    with StellenHamburgParser(_http_post=post) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 2


def test_discover_stub_title_matches_descriptor_position_title() -> None:
    post = _make_post([_search_body([_item("1", "Data Scientist")])])
    with StellenHamburgParser(_http_post=post) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.title == "Data Scientist"


def test_discover_stub_source_is_stellen_hamburg() -> None:
    post = _make_post([_search_body([_item()])])
    with StellenHamburgParser(_http_post=post) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.source == "stellen.hamburg"


def test_discover_stub_language_is_de() -> None:
    post = _make_post([_search_body([_item()])])
    with StellenHamburgParser(_http_post=post) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.language == "de"


def test_discover_stub_company_from_organization_name() -> None:
    post = _make_post([_search_body([_item(company="Finanzbehörde Hamburg")])])
    with StellenHamburgParser(_http_post=post) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company == "Finanzbehörde Hamburg"


def test_discover_stub_location_from_position_location() -> None:
    post = _make_post([_search_body([_item(location="Hamburg")])])
    with StellenHamburgParser(_http_post=post) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location == "Hamburg"


def test_discover_stub_url_contains_object_id() -> None:
    post = _make_post([_search_body([_item("99999")])])
    with StellenHamburgParser(_http_post=post) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert "99999" in stub.url


def test_discover_stub_company_none_when_organization_absent() -> None:
    post = _make_post([_search_body([_item(company=None)])])
    with StellenHamburgParser(_http_post=post) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company is None


def test_discover_stub_location_none_when_position_location_absent() -> None:
    post = _make_post([_search_body([_item(location=None)])])
    with StellenHamburgParser(_http_post=post) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location is None


# ---------------------------------------------------------------------------
# discover — pagination
# ---------------------------------------------------------------------------


def test_discover_paginates_until_total_reached() -> None:
    page0 = _search_body([_item("1"), _item("2")], total=4)
    page1 = _search_body([_item("3"), _item("4")], total=4)
    post = _make_post([page0, page1])
    with StellenHamburgParser(_http_post=post) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 4


def test_discover_stops_on_empty_items() -> None:
    body = json.dumps(
        {
            "SearchResult": {
                "SearchResultCount": 0,
                "SearchResultCountAll": 0,
                "SearchResultItems": [],
            }
        }
    ).encode()
    post = _make_post([body])
    with StellenHamburgParser(_http_post=post) as p:
        stubs = list(p.discover(_query()))
    assert stubs == []


def test_discover_stops_on_null_search_result_items() -> None:
    body = json.dumps(
        {
            "SearchResult": {
                "SearchResultCount": 0,
                "SearchResultCountAll": 0,
                "SearchResultItems": None,
            }
        }
    ).encode()
    post = _make_post([body])
    with StellenHamburgParser(_http_post=post) as p:
        stubs = list(p.discover(_query()))
    assert stubs == []


# ---------------------------------------------------------------------------
# discover — deduplication
# ---------------------------------------------------------------------------


def test_discover_deduplicates_same_object_id() -> None:
    shared = _item("same_id", "Dev")
    page0 = _search_body([shared], total=2)
    page1 = _search_body([shared], total=2)
    post = _make_post([page0, page1])
    with StellenHamburgParser(_http_post=post) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 1


# ---------------------------------------------------------------------------
# discover — max_results cap
# ---------------------------------------------------------------------------


def test_discover_respects_max_results() -> None:
    items = [_item(str(i)) for i in range(10)]
    post = _make_post([_search_body(items, total=10)])
    with StellenHamburgParser(_http_post=post) as p:
        stubs = list(p.discover(_query(max_results=3)))
    assert len(stubs) == 3


# ---------------------------------------------------------------------------
# discover — error handling
# ---------------------------------------------------------------------------


def test_discover_raises_parser_error_on_http_failure() -> None:
    from application_pipeline.parsers import ParserError

    def failing_post(url: str, body: bytes, timeout: float) -> bytes:
        raise OSError("refused")

    with StellenHamburgParser(_http_post=failing_post, _retries=1) as p:
        with pytest.raises(ParserError):
            list(p.discover(_query()))


# ---------------------------------------------------------------------------
# enrich — raw description
# ---------------------------------------------------------------------------


def test_enrich_returns_position_with_raw_description(stub: PositionStub) -> None:
    get = _make_get([_detail_html(description="We are hiring.")])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description == "We are hiring."


def test_enrich_strips_html_tags_from_description(stub: PositionStub) -> None:
    get = _make_get([_detail_html(description="<p>Hello</p><p>World</p>")])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "<p>" not in pos.raw_description
    assert "Hello" in pos.raw_description
    assert "World" in pos.raw_description


def test_enrich_decodes_html_entities_in_description(stub: PositionStub) -> None:
    get = _make_get([_detail_html(description="Geh&auml;lter &amp; Benefits")])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "Gehälter" in pos.raw_description
    assert "&amp;" not in pos.raw_description


def test_enrich_empty_description_when_no_jsonld(stub: PositionStub) -> None:
    get = _make_get([b"<html><body><p>No structured data</p></body></html>"])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description == ""


# ---------------------------------------------------------------------------
# enrich — dates
# ---------------------------------------------------------------------------


def test_enrich_parses_posted_date(stub: PositionStub) -> None:
    get = _make_get([_detail_html(date_posted="2026-03-15")])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.posted_date == date(2026, 3, 15)


def test_enrich_parses_deadline(stub: PositionStub) -> None:
    get = _make_get([_detail_html(valid_through="2026-05-30")])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.deadline == date(2026, 5, 30)


def test_enrich_posted_date_none_when_field_absent(stub: PositionStub) -> None:
    get = _make_get([b"<html><body></body></html>"])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.posted_date is None


def test_enrich_deadline_none_when_field_absent(stub: PositionStub) -> None:
    get = _make_get([_detail_html()])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.deadline is None


# ---------------------------------------------------------------------------
# enrich — employment_type
# ---------------------------------------------------------------------------


def test_enrich_maps_full_time_employment_type(stub: PositionStub) -> None:
    get = _make_get([_detail_html(employment_type="FULL_TIME")])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.employment_type == "full-time"


def test_enrich_maps_part_time_employment_type(stub: PositionStub) -> None:
    get = _make_get([_detail_html(employment_type="PART_TIME")])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.employment_type == "part-time"


def test_enrich_employment_type_none_when_absent(stub: PositionStub) -> None:
    get = _make_get([_detail_html()])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.employment_type is None


# ---------------------------------------------------------------------------
# enrich — error handling
# ---------------------------------------------------------------------------


def test_enrich_raises_parser_error_on_http_failure() -> None:
    from application_pipeline.parsers import ParserError

    stub = PositionStub(
        url="https://stellen.hamburg.de/index.php?ac=jobad&id=99",
        title="Dev",
        source="stellen_hamburg",
    )

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with StellenHamburgParser(_http_get=failing_get, _retries=1) as p:
        with pytest.raises(ParserError):
            p.enrich(stub)


# ---------------------------------------------------------------------------
# enrich — stub reference preserved
# ---------------------------------------------------------------------------


def test_enrich_position_references_original_stub(stub: PositionStub) -> None:
    get = _make_get([_detail_html()])
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.stub is stub
