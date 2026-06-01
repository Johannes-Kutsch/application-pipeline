from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import Parser, ParserQuery, PositionStub
from application_pipeline.parsers.http import ParserHttp
from application_pipeline.parsers.stellen_hamburg_api import (
    StellenHamburgParser,
    parser_class,
)
from application_pipeline.parsers.types import City

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NO_SLEEP = lambda _: None  # noqa: E731


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


def _make_search_get(responses: list[bytes]) -> Callable[[str, float], bytes]:
    it = iter(responses)

    def http_get(url: str, timeout: float) -> bytes:
        return next(it)

    return http_get


def _make_get(responses: list[bytes]) -> Callable[[str, float], bytes]:
    it = iter(responses)

    def http_get(url: str, timeout: float) -> bytes:
        return next(it)

    return http_get


def _http(
    run_log: RunLog, http_get: Callable[[str, float], bytes], *, retries: int = 3
) -> ParserHttp:
    return ParserHttp(
        run_log=run_log, _http_get=http_get, retries=retries, _sleep=_NO_SLEEP
    )


def _query(**kwargs: object) -> ParserQuery:
    defaults: dict = {
        "keyword": "python",
        "location": City("hamburg"),
    }
    defaults.update(kwargs)
    return ParserQuery(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


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


def test_parser_satisfies_parser_protocol(run_log: RunLog) -> None:
    p = StellenHamburgParser(run_log=run_log)
    assert isinstance(p, Parser)


def test_parser_is_usable_as_context_manager(run_log: RunLog) -> None:
    with StellenHamburgParser(run_log=run_log) as p:
        assert isinstance(p, StellenHamburgParser)


# ---------------------------------------------------------------------------
# discover — basic stub fields
# ---------------------------------------------------------------------------


def test_discover_yields_one_stub_per_result(run_log: RunLog) -> None:
    get = _make_search_get(
        [
            _search_body([_item("1", "Dev A"), _item("2", "Dev B")]),
        ]
    )
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 2


def test_discover_stub_title_matches_descriptor_position_title(run_log: RunLog) -> None:
    get = _make_search_get([_search_body([_item("1", "Data Scientist")])])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.title == "Data Scientist"


def test_discover_stub_source_is_stellen_hamburg(run_log: RunLog) -> None:
    get = _make_search_get([_search_body([_item()])])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.source == "stellen.hamburg"


def test_discover_stub_company_from_organization_name(run_log: RunLog) -> None:
    get = _make_search_get([_search_body([_item(company="Finanzbehörde Hamburg")])])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company == "Finanzbehörde Hamburg"


def test_discover_stub_location_from_position_location(run_log: RunLog) -> None:
    get = _make_search_get([_search_body([_item(location="Hamburg")])])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location == "Hamburg"


def test_discover_stub_url_contains_object_id(run_log: RunLog) -> None:
    get = _make_search_get([_search_body([_item("99999")])])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert "99999" in stub.url


def test_discover_stub_company_none_when_organization_absent(run_log: RunLog) -> None:
    get = _make_search_get([_search_body([_item(company=None)])])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company is None


def test_discover_stub_location_none_when_position_location_absent(
    run_log: RunLog,
) -> None:
    get = _make_search_get([_search_body([_item(location=None)])])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location is None


# ---------------------------------------------------------------------------
# discover — pagination
# ---------------------------------------------------------------------------


def test_discover_paginates_until_total_reached(run_log: RunLog) -> None:
    page0 = _search_body([_item("1"), _item("2")], total=4)
    page1 = _search_body([_item("3"), _item("4")], total=4)
    get = _make_search_get([page0, page1])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 4


def test_discover_stops_on_empty_items(run_log: RunLog) -> None:
    body = json.dumps(
        {
            "SearchResult": {
                "SearchResultCount": 0,
                "SearchResultCountAll": 0,
                "SearchResultItems": [],
            }
        }
    ).encode()
    get = _make_search_get([body])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        stubs = list(p.discover(_query()))
    assert stubs == []


def test_discover_stops_on_null_search_result_items(run_log: RunLog) -> None:
    body = json.dumps(
        {
            "SearchResult": {
                "SearchResultCount": 0,
                "SearchResultCountAll": 0,
                "SearchResultItems": None,
            }
        }
    ).encode()
    get = _make_search_get([body])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        stubs = list(p.discover(_query()))
    assert stubs == []


# ---------------------------------------------------------------------------
# discover — full pagination
# ---------------------------------------------------------------------------


def test_discover_returns_all_results_without_cap(run_log: RunLog) -> None:
    items = [_item(str(i)) for i in range(10)]
    get = _make_search_get([_search_body(items, total=10)])
    with StellenHamburgParser(run_log=run_log, _http=_http(run_log, get)) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 10


# ---------------------------------------------------------------------------
# discover — error handling
# ---------------------------------------------------------------------------


def test_discover_raises_parser_error_on_http_failure(run_log: RunLog) -> None:
    from application_pipeline.parsers import ParserError

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with StellenHamburgParser(
        run_log=run_log,
        _http=_http(run_log, failing_get, retries=1),
    ) as p:
        with pytest.raises(ParserError):
            list(p.discover(_query()))
