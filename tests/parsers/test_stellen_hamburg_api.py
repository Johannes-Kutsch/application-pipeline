from __future__ import annotations

import json
import logging
import urllib.parse
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
import httpx
import respx

from application_pipeline.parsers.types import (
    City,
    EnrichFailedError,
    NotServedQuery,
    Remote,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "stellen_hamburg"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _make_get(responses: dict[str, bytes]) -> Callable[[str, float], bytes]:
    def http_get(url: str, timeout: float) -> bytes:
        for key, body in responses.items():
            if key in url:
                return body
        raise OSError(f"unexpected URL: {url}")

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
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


@pytest.fixture
def search_json() -> bytes:
    return _load("search.json")


@pytest.fixture
def detail_html() -> bytes:
    return _load("detail.html")


@pytest.fixture
def stub() -> PositionStub:
    return PositionStub(
        url="https://stellen.hamburg.de/jobad/11111",
        title="Softwareentwickler/in (m/w/d)",
        source="stellen.hamburg",
    )


# ---------------------------------------------------------------------------
# parser_class / Protocol
# ---------------------------------------------------------------------------


def test_parser_class_attribute_is_stellen_hamburg_parser() -> None:
    assert parser_class is StellenHamburgParser


def test_parser_satisfies_parser_protocol(run_log: RunLog) -> None:
    p = StellenHamburgParser(run_log=run_log)
    assert isinstance(p, Parser)


def test_parser_is_usable_as_context_manager(run_log: RunLog) -> None:
    with StellenHamburgParser(run_log=run_log) as p:
        assert isinstance(p, StellenHamburgParser)


# ---------------------------------------------------------------------------
# discover — location short-circuit
# ---------------------------------------------------------------------------


def test_discover_remote_location_yields_not_served_sentinel(run_log: RunLog) -> None:
    def never_called(url: str, timeout: float) -> bytes:
        raise AssertionError("should not GET")

    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=never_called)
    ) as p:
        stubs = list(p.discover(_query(location=Remote())))

    assert stubs == [NotServedQuery()]


def test_discover_yields_not_served_sentinel_when_location_unmapped(
    run_log: RunLog,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def never_called(url: str, timeout: float) -> bytes:
        raise AssertionError("should not GET")

    with caplog.at_level(
        logging.INFO, logger="application_pipeline.parsers.stellen_hamburg_api"
    ):
        with StellenHamburgParser(
            run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=never_called)
        ) as p:
            stubs = list(p.discover(_query(location=City("berlin"))))

    assert stubs == [NotServedQuery()]
    assert not any("not_served" in r.getMessage() for r in caplog.records)


def test_discover_unmapped_location_does_not_make_http_request(run_log: RunLog) -> None:
    def never_called(url: str, timeout: float) -> bytes:
        raise AssertionError("should not GET")

    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=never_called)
    ) as p:
        list(p.discover(_query(location=City("munich"))))


def test_discover_normalizes_location_case(run_log: RunLog, search_json: bytes) -> None:
    get = _make_get({"api-stellen": search_json})
    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        stubs = list(p.discover(_query(location=City("Hamburg"))))
    assert len(stubs) == 2


# ---------------------------------------------------------------------------
# discover — stub fields
# ---------------------------------------------------------------------------


def test_discover_yields_stubs_from_search_response(
    run_log: RunLog, search_json: bytes
) -> None:
    get = _make_get({"api-stellen": search_json})
    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 2


def test_discover_stub_source_is_display_name(
    run_log: RunLog, search_json: bytes
) -> None:
    get = _make_get({"api-stellen": search_json})
    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.source == "stellen.hamburg"


def test_discover_stub_title_extracted(run_log: RunLog, search_json: bytes) -> None:
    get = _make_get({"api-stellen": search_json})
    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.title == "Softwareentwickler/in (m/w/d)"


def test_discover_stub_url_extracted(run_log: RunLog, search_json: bytes) -> None:
    get = _make_get({"api-stellen": search_json})
    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert "jobad/11111" in stub.url


def test_discover_stub_company_extracted(run_log: RunLog, search_json: bytes) -> None:
    get = _make_get({"api-stellen": search_json})
    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company == "Stadtverwaltung Hamburg"


def test_discover_stub_location_extracted(run_log: RunLog, search_json: bytes) -> None:
    get = _make_get({"api-stellen": search_json})
    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location == "Hamburg"


# ---------------------------------------------------------------------------
# discover — max_results cap
# ---------------------------------------------------------------------------


def test_discover_respects_max_results(run_log: RunLog, search_json: bytes) -> None:
    get = _make_get({"api-stellen": search_json})
    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        stubs = list(p.discover(_query(max_results=1)))
    assert len(stubs) == 1


# ---------------------------------------------------------------------------
# discover — GET request shape with data query parameter
# ---------------------------------------------------------------------------


def test_discover_issues_get_with_data_param_carrying_search_criteria(
    run_log: RunLog,
    search_json: bytes,
) -> None:
    captured_urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        captured_urls.append(url)
        return search_json

    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query(keyword="python")))

    search_calls = [u for u in captured_urls if "api-stellen.hamburg.de" in u]
    assert search_calls, "no GET to api-stellen.hamburg.de was made"

    parsed = urllib.parse.urlparse(search_calls[0])
    params = urllib.parse.parse_qs(parsed.query)
    assert "data" in params, f"no 'data' param in: {search_calls[0]}"
    data = json.loads(params["data"][0])
    assert data.get("SearchCriteria") == [
        {
            "CriterionName": "PositionFormattedDescription.Content",
            "CriterionValue": "python",
        }
    ]


def test_discover_pagination_second_page_sends_page_number_two(run_log: RunLog) -> None:
    def _body(items: list[dict], total: int) -> bytes:
        return json.dumps(
            {
                "SearchResult": {
                    "SearchResultItems": items,
                    "SearchResultCountAll": total,
                }
            }
        ).encode()

    def _item(obj_id: str) -> dict:
        return {
            "MatchedObjectId": obj_id,
            "MatchedObjectDescriptor": {"PositionTitle": f"Job {obj_id}"},
        }

    page1 = _body([_item("1")], total=50)
    page2 = _body([], total=50)
    responses = iter([page1, page2])
    captured_urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        captured_urls.append(url)
        return next(responses)

    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query()))

    search_calls = [u for u in captured_urls if "api-stellen.hamburg.de" in u]
    assert len(search_calls) == 2

    parsed2 = urllib.parse.urlparse(search_calls[1])
    data2 = json.loads(urllib.parse.parse_qs(parsed2.query)["data"][0])
    assert data2["PageNumber"] == 2
    assert data2["PageSize"] == 25


# ---------------------------------------------------------------------------
# discover — discover_page heartbeat
# ---------------------------------------------------------------------------


def test_discover_emits_discover_page_heartbeat_per_page(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)

    def _sh_item(obj_id: str) -> dict:
        return {
            "MatchedObjectId": obj_id,
            "MatchedObjectDescriptor": {"PositionTitle": f"Job {obj_id}"},
        }

    def _sh_body(items: list[dict], total: int) -> bytes:
        return json.dumps(
            {
                "SearchResult": {
                    "SearchResultItems": items,
                    "SearchResultCountAll": total,
                }
            }
        ).encode()

    responses = iter(
        [_sh_body([_sh_item("1")], total=2), _sh_body([_sh_item("2")], total=2)]
    )

    def sequential_get(url: str, timeout: float) -> bytes:
        return next(responses)

    with StellenHamburgParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=sequential_get)
    ) as p:
        stubs = list(p.discover(_query()))

    assert len(stubs) == 2
    events_rows = [
        json.loads(line)
        for line in (tmp_path / "parser" / "stellen_hamburg_api.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    page_rows = [row for row in events_rows if row.get("event") == "discover_page"]
    assert len(page_rows) == 2
    starts = [row["start"] for row in page_rows]
    assert starts == sorted(starts)
    assert starts[0] < starts[1]


# ---------------------------------------------------------------------------
# discover — error handling
# ---------------------------------------------------------------------------


def test_discover_raises_parser_error_on_http_failure(run_log: RunLog) -> None:
    from application_pipeline.parsers import ParserError

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("connection refused")

    with StellenHamburgParser(
        run_log=run_log,
        _http=ParserHttp(run_log=run_log, _http_get=failing_get, retries=1),
    ) as p:
        with pytest.raises(ParserError):
            list(p.discover(_query()))


# ---------------------------------------------------------------------------
# enrich — HTTP error semantics
# ---------------------------------------------------------------------------


def test_enrich_raises_enrich_failed_error_on_404(
    run_log: RunLog, tmp_path: Path, stub: PositionStub
) -> None:
    with respx.mock:
        respx.get(stub.url).mock(return_value=httpx.Response(404))
        with StellenHamburgParser(run_log=run_log, failures_dir=tmp_path) as p:
            with pytest.raises(EnrichFailedError):
                p.enrich(stub)


def test_enrich_propagates_transient_http_error_on_503(
    run_log: RunLog, tmp_path: Path, stub: PositionStub
) -> None:
    with respx.mock:
        respx.get(stub.url).mock(return_value=httpx.Response(503))
        with StellenHamburgParser(run_log=run_log, failures_dir=tmp_path) as p:
            with pytest.raises(httpx.HTTPStatusError):
                p.enrich(stub)
