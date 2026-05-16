from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pytest

import application_pipeline.parser_log as parser_log
from application_pipeline.parsers import Parser, ParserQuery, PositionStub
from application_pipeline.parsers.types import City, NotServedQuery, Remote
from application_pipeline.parsers.http import HttpGet
from application_pipeline.parsers.stellen_hamburg_api import (
    HttpPost,
    StellenHamburgParser,
    parser_class,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "stellen_hamburg"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _make_post(response: bytes) -> HttpPost:
    def http_post(url: str, body: bytes, timeout: float) -> bytes:
        return response

    return http_post


def _make_get(responses: dict[str, bytes]) -> HttpGet:
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


def test_parser_satisfies_parser_protocol() -> None:
    p = StellenHamburgParser()
    assert isinstance(p, Parser)


def test_parser_is_usable_as_context_manager() -> None:
    with StellenHamburgParser() as p:
        assert isinstance(p, StellenHamburgParser)


# ---------------------------------------------------------------------------
# discover — location short-circuit
# ---------------------------------------------------------------------------


def test_discover_remote_location_yields_not_served_sentinel() -> None:
    def never_called(url: str, body: bytes, timeout: float) -> bytes:
        raise AssertionError("should not POST")

    with StellenHamburgParser(_http_post=never_called) as p:
        stubs = list(p.discover(_query(location=Remote())))

    assert stubs == [NotServedQuery()]


def test_discover_yields_not_served_sentinel_when_location_unmapped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def never_called(url: str, body: bytes, timeout: float) -> bytes:
        raise AssertionError("should not POST")

    with caplog.at_level(
        logging.INFO, logger="application_pipeline.parsers.stellen_hamburg_api"
    ):
        with StellenHamburgParser(_http_post=never_called) as p:
            stubs = list(p.discover(_query(location=City("berlin"))))

    assert stubs == [NotServedQuery()]
    assert not any("not_served" in r.getMessage() for r in caplog.records)


def test_discover_unmapped_location_does_not_make_http_request() -> None:
    def never_called(url: str, body: bytes, timeout: float) -> bytes:
        raise AssertionError("should not POST")

    with StellenHamburgParser(_http_post=never_called) as p:
        list(p.discover(_query(location=City("munich"))))


def test_discover_normalizes_location_case(search_json: bytes) -> None:
    post = _make_post(search_json)
    with StellenHamburgParser(_http_post=post) as p:
        stubs = list(p.discover(_query(location=City("Hamburg"))))
    assert len(stubs) == 2


# ---------------------------------------------------------------------------
# discover — stub fields
# ---------------------------------------------------------------------------


def test_discover_yields_stubs_from_search_response(search_json: bytes) -> None:
    post = _make_post(search_json)
    with StellenHamburgParser(_http_post=post) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 2


def test_discover_stub_source_is_display_name(search_json: bytes) -> None:
    post = _make_post(search_json)
    with StellenHamburgParser(_http_post=post) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.source == "stellen.hamburg"


def test_discover_stub_title_extracted(search_json: bytes) -> None:
    post = _make_post(search_json)
    with StellenHamburgParser(_http_post=post) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.title == "Softwareentwickler/in (m/w/d)"


def test_discover_stub_url_extracted(search_json: bytes) -> None:
    post = _make_post(search_json)
    with StellenHamburgParser(_http_post=post) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert "jobad/11111" in stub.url


def test_discover_stub_company_extracted(search_json: bytes) -> None:
    post = _make_post(search_json)
    with StellenHamburgParser(_http_post=post) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company == "Stadtverwaltung Hamburg"


def test_discover_stub_location_extracted(search_json: bytes) -> None:
    post = _make_post(search_json)
    with StellenHamburgParser(_http_post=post) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location == "Hamburg"


# ---------------------------------------------------------------------------
# discover — max_results cap
# ---------------------------------------------------------------------------


def test_discover_respects_max_results(search_json: bytes) -> None:
    post = _make_post(search_json)
    with StellenHamburgParser(_http_post=post) as p:
        stubs = list(p.discover(_query(max_results=1)))
    assert len(stubs) == 1


# ---------------------------------------------------------------------------
# discover — pagination uses FirstItem / CountItem
# ---------------------------------------------------------------------------


def test_discover_pagination_uses_first_item_and_count_item(
    search_json: bytes,
) -> None:
    posted_bodies: list[dict] = []

    def capturing_post(url: str, body: bytes, timeout: float) -> bytes:
        posted_bodies.append(json.loads(body))
        return search_json

    with StellenHamburgParser(_http_post=capturing_post) as p:
        list(p.discover(_query()))

    params = posted_bodies[0]["SearchParameters"]
    assert "FirstItem" in params
    assert "CountItem" in params
    assert "Offset" not in params
    assert "NumberOfResults" not in params


# ---------------------------------------------------------------------------
# discover — POST headers
# ---------------------------------------------------------------------------


def test_discover_default_post_sends_origin_and_referer() -> None:
    import httpx
    import respx

    from application_pipeline.parsers.stellen_hamburg_api import _default_http_post

    with respx.mock:
        route = respx.post("https://api-stellen.hamburg.de/search/").mock(
            return_value=httpx.Response(200, json={"SearchResult": {}})
        )
        body = json.dumps({"SearchParameters": {}}).encode()
        _default_http_post("https://api-stellen.hamburg.de/search/", body, 5.0)

    req = route.calls.last.request
    assert req.headers.get("origin") == "https://stellen.hamburg.de"
    assert req.headers.get("referer") == "https://stellen.hamburg.de/"


# ---------------------------------------------------------------------------
# discover — discover_page heartbeat
# ---------------------------------------------------------------------------


def test_discover_emits_discover_page_heartbeat_per_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parser_log, "_logs_dir", tmp_path)

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

    def sequential_post(url: str, body: bytes, timeout: float) -> bytes:
        return next(responses)

    with StellenHamburgParser(_http_post=sequential_post) as p:
        stubs = list(p.discover(_query()))

    assert len(stubs) == 2
    log_content = (tmp_path / "stellen_hamburg_api.log").read_text(encoding="utf-8")
    lines = [ln for ln in log_content.splitlines() if "discover_page" in ln]
    assert len(lines) == 2
    starts = [int(ln.split("start=")[1].split()[0]) for ln in lines]
    assert starts == sorted(starts)
    assert starts[0] < starts[1]


# ---------------------------------------------------------------------------
# discover — error handling
# ---------------------------------------------------------------------------


def test_discover_raises_parser_error_on_http_failure() -> None:
    from application_pipeline.parsers import ParserError

    def failing_post(url: str, body: bytes, timeout: float) -> bytes:
        raise OSError("connection refused")

    with StellenHamburgParser(_http_post=failing_post, _retries=1) as p:
        with pytest.raises(ParserError):
            list(p.discover(_query()))


# ---------------------------------------------------------------------------
# enrich — description extraction
# ---------------------------------------------------------------------------


def test_enrich_returns_position_with_raw_description(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get({"jobad": detail_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description != ""


def test_enrich_description_has_no_html_tags(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get({"jobad": detail_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "<p>" not in pos.raw_description
    assert "<strong>" not in pos.raw_description


def test_enrich_description_decodes_html_entities(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get({"jobad": detail_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "&uuml;" not in pos.raw_description
    assert "ü" in pos.raw_description


def test_enrich_employment_type_full_time(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get({"jobad": detail_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.employment_type == "full-time"


def test_enrich_posted_date_parsed(stub: PositionStub, detail_html: bytes) -> None:
    get = _make_get({"jobad": detail_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.posted_date == date(2026, 4, 1)


def test_enrich_deadline_parsed(stub: PositionStub, detail_html: bytes) -> None:
    get = _make_get({"jobad": detail_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.deadline == date(2026, 6, 30)


def test_enrich_position_references_original_stub(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get({"jobad": detail_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.stub is stub


# ---------------------------------------------------------------------------
# enrich — error handling
# ---------------------------------------------------------------------------


def test_enrich_raises_parser_error_on_http_failure(stub: PositionStub) -> None:
    from application_pipeline.parsers import ParserError

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("timeout")

    with StellenHamburgParser(_http_get=failing_get, _retries=1) as p:
        with pytest.raises(ParserError):
            p.enrich(stub)


# ---------------------------------------------------------------------------
# enrich — list-wrapped JSON-LD payload
# ---------------------------------------------------------------------------


@pytest.fixture
def detail_list_wrapped_html() -> bytes:
    return _load("detail_list_wrapped.html")


@pytest.fixture
def detail_no_job_posting_html() -> bytes:
    return _load("detail_no_job_posting.html")


def test_enrich_list_wrapped_jsonld_returns_nonempty_description(
    stub: PositionStub, detail_list_wrapped_html: bytes
) -> None:
    get = _make_get({"jobad": detail_list_wrapped_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description != ""


def test_enrich_list_wrapped_jsonld_description_has_no_html_tags(
    stub: PositionStub, detail_list_wrapped_html: bytes
) -> None:
    get = _make_get({"jobad": detail_list_wrapped_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "<p>" not in pos.raw_description
    assert "<strong>" not in pos.raw_description


def test_enrich_list_wrapped_jsonld_decodes_umlauts(
    stub: PositionStub, detail_list_wrapped_html: bytes
) -> None:
    get = _make_get({"jobad": detail_list_wrapped_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "&uuml;" not in pos.raw_description
    assert "ü" in pos.raw_description


def test_enrich_list_selects_first_job_posting_entry(
    stub: PositionStub, detail_list_wrapped_html: bytes
) -> None:
    get = _make_get({"jobad": detail_list_wrapped_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.posted_date == date(2026, 5, 1)
    assert pos.deadline == date(2026, 7, 31)
    assert pos.employment_type == "full-time"


def test_enrich_list_without_job_posting_yields_empty_description(
    stub: PositionStub, detail_no_job_posting_html: bytes
) -> None:
    get = _make_get({"jobad": detail_no_job_posting_html})
    with StellenHamburgParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description == ""
