from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from application_pipeline.parsers import Parser, ParserQuery, PositionStub
from application_pipeline.parsers.types import City, Remote
from application_pipeline.parsers.http import HttpGet
from application_pipeline.parsers import jobs_beim_staat_html as parser_module
from application_pipeline.parsers.jobs_beim_staat_html import (
    JobsBeimStaatParser,
    _parse_posted_date,
    parser_class,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "jobs_beim_staat"
_TODAY = date(2026, 5, 8)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _make_get(responses: list[bytes]) -> HttpGet:
    it = iter(responses)

    def http_get(url: str, timeout: float) -> bytes:
        return next(it)

    return http_get


def _jobs_envelope(jobs_html: bytes) -> bytes:
    return json.dumps({"jobs": jobs_html.decode("utf-8"), "count": -1}).encode()


def _empty_envelope() -> bytes:
    return json.dumps({"jobs": "", "count": 0}).encode()


def _query(**kwargs: object) -> ParserQuery:
    defaults: dict = {
        "keyword": "python",
        "location": City("hamburg"),
        "max_results": 100,
    }
    defaults.update(kwargs)
    return ParserQuery(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def list_html() -> bytes:
    return _load("list.html")


@pytest.fixture
def detail_html() -> bytes:
    return _load("detail.html")


@pytest.fixture
def stub() -> PositionStub:
    return PositionStub(
        url="https://www.jobs-beim-staat.de/stellenangebote/1001",
        title="Softwareentwickler/in (m/w/d)",
        source="jobs-beim-staat",
    )


# ---------------------------------------------------------------------------
# _parse_posted_date — all patterns
# ---------------------------------------------------------------------------


def test_parse_posted_date_heute_returns_today() -> None:
    assert _parse_posted_date("heute", _TODAY) == _TODAY


def test_parse_posted_date_gestern_returns_yesterday() -> None:
    assert _parse_posted_date("gestern", _TODAY) == date(2026, 5, 7)


def test_parse_posted_date_vor_n_tagen_subtracts_days() -> None:
    assert _parse_posted_date("vor 3 Tagen", _TODAY) == date(2026, 5, 5)


def test_parse_posted_date_vor_1_tag_singular() -> None:
    assert _parse_posted_date("vor 1 Tag", _TODAY) == date(2026, 5, 7)


def test_parse_posted_date_vor_n_wochen_subtracts_weeks() -> None:
    assert _parse_posted_date("vor 2 Wochen", _TODAY) == date(2026, 4, 24)


def test_parse_posted_date_vor_1_woche_singular() -> None:
    assert _parse_posted_date("vor 1 Woche", _TODAY) == date(2026, 5, 1)


def test_parse_posted_date_dmy_format() -> None:
    assert _parse_posted_date("15.04.2026", _TODAY) == date(2026, 4, 15)


def test_parse_posted_date_unparseable_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.INFO):
        result = _parse_posted_date("irgendwann mal", _TODAY)
    assert result is None
    assert "unparseable_date" in caplog.text
    assert "jobs_beim_staat_html" in caplog.text
    assert "irgendwann mal" in caplog.text


def test_parse_posted_date_case_insensitive_heute() -> None:
    assert _parse_posted_date("Heute", _TODAY) == _TODAY


def test_parse_posted_date_case_insensitive_gestern() -> None:
    assert _parse_posted_date("Gestern", _TODAY) == date(2026, 5, 7)


# ---------------------------------------------------------------------------
# LocationCoverage module-level symbols
# ---------------------------------------------------------------------------


def test_module_serves_any_city() -> None:
    assert parser_module.serves("hamburg") is True


def test_module_serves_unknown_city_still_true() -> None:
    assert parser_module.serves("atlantis") is True


def test_module_to_wire_passes_through_name() -> None:
    assert parser_module.to_wire("hamburg") == "hamburg"


def test_module_to_wire_passes_through_normalized_umlaut() -> None:
    assert parser_module.to_wire("köln") == "köln"


def test_module_serves_remote_is_true() -> None:
    assert parser_module.serves_remote is True


def test_module_remote_wire_returns_homeoffice() -> None:
    assert parser_module.remote_wire() == "homeoffice"


# ---------------------------------------------------------------------------
# parser_class attribute / Protocol
# ---------------------------------------------------------------------------


def test_parser_class_attribute_is_jobs_beim_staat_parser() -> None:
    assert parser_class is JobsBeimStaatParser


def test_parser_satisfies_parser_protocol() -> None:
    p = JobsBeimStaatParser()
    assert isinstance(p, Parser)


def test_parser_is_usable_as_context_manager() -> None:
    with JobsBeimStaatParser() as p:
        assert isinstance(p, JobsBeimStaatParser)


# ---------------------------------------------------------------------------
# discover — REST URL construction
# ---------------------------------------------------------------------------


def test_discover_url_contains_sort_radius_viewtype(list_html: bytes) -> None:
    fetched_urls: list[str] = []
    responses = iter([_jobs_envelope(list_html), _empty_envelope()])

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return next(responses)

    with JobsBeimStaatParser(_http_get=capturing_get) as p:
        list(p.discover(_query()))

    first = fetched_urls[0]
    assert "sort=date" in first
    assert "radius=20" in first
    assert "viewType=card" in first


def test_discover_url_q_is_empty_for_star_keyword(list_html: bytes) -> None:
    fetched_urls: list[str] = []
    responses = iter([_jobs_envelope(list_html), _empty_envelope()])

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return next(responses)

    with JobsBeimStaatParser(_http_get=capturing_get) as p:
        list(p.discover(_query(keyword="*")))

    first = fetched_urls[0]
    assert "q=" in first
    assert "q=%2A" not in first
    assert "q=*" not in first


def test_discover_url_place_is_homeoffice_for_remote(list_html: bytes) -> None:
    fetched_urls: list[str] = []
    responses = iter([_jobs_envelope(list_html), _empty_envelope()])

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return next(responses)

    with JobsBeimStaatParser(_http_get=capturing_get) as p:
        list(p.discover(_query(location=Remote())))

    assert any("place=homeoffice" in u for u in fetched_urls)


def test_discover_url_place_uses_normalized_city_name(list_html: bytes) -> None:
    fetched_urls: list[str] = []
    responses = iter([_jobs_envelope(list_html), _empty_envelope()])

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return next(responses)

    with JobsBeimStaatParser(_http_get=capturing_get) as p:
        list(p.discover(_query(location=City("Hamburg"))))

    assert any("place=hamburg" in u for u in fetched_urls)


# ---------------------------------------------------------------------------
# discover — basic stub fields from fixture
# ---------------------------------------------------------------------------


def test_discover_yields_21_stubs_from_list_page(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 21


def test_discover_stub_source_is_jobs_beim_staat(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert stub.source == "jobs-beim-staat"


def test_discover_stub_title_extracted(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert stub.title == "Softwareentwickler/in (m/w/d)"


def test_discover_stub_url_points_to_stellenangebote(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert "stellenangebote/1001" in stub.url


def test_discover_stub_company_extracted_from_data_attribute(
    list_html: bytes,
) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert stub.company == "Hamburger IT-Serviceteam GmbH"


def test_discover_stub_location_extracted(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert stub.location == "Hamburg"


def test_discover_stub_language_is_de(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert stub.language == "de"


# ---------------------------------------------------------------------------
# discover — pagination and stop conditions
# ---------------------------------------------------------------------------


def test_discover_stops_when_jobs_fragment_has_no_cards(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 21


def test_discover_paginates_across_multiple_pages(list_html: bytes) -> None:
    get = _make_get(
        [_jobs_envelope(list_html), _jobs_envelope(list_html), _empty_envelope()]
    )
    with JobsBeimStaatParser(_http_get=get) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 42


def test_discover_respects_max_results(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        stubs = list(p.discover(_query(max_results=5)))
    assert len(stubs) == 5


def test_discover_max_results_stops_mid_page(list_html: bytes) -> None:
    get = _make_get(
        [_jobs_envelope(list_html), _jobs_envelope(list_html), _empty_envelope()]
    )
    with JobsBeimStaatParser(_http_get=get) as p:
        stubs = list(p.discover(_query(max_results=30)))
    assert len(stubs) == 30


# ---------------------------------------------------------------------------
# discover — NotServed arm (empty city name normalizes to None)
# ---------------------------------------------------------------------------


def test_discover_not_served_for_empty_city_yields_nothing() -> None:
    def never_called(url: str, timeout: float) -> bytes:
        raise AssertionError("should not fetch")

    with JobsBeimStaatParser(_http_get=never_called) as p:
        stubs = list(p.discover(_query(location=City(""))))

    assert stubs == []


# ---------------------------------------------------------------------------
# discover — error handling
# ---------------------------------------------------------------------------


def test_discover_raises_parser_error_on_http_failure() -> None:
    from application_pipeline.parsers import ParserError

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("connection refused")

    with JobsBeimStaatParser(_http_get=failing_get, _retries=1) as p:
        with pytest.raises(ParserError):
            list(p.discover(_query()))


# ---------------------------------------------------------------------------
# enrich — description extraction
# ---------------------------------------------------------------------------


def test_enrich_returns_position_with_raw_description(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get([detail_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description != ""


def test_enrich_description_contains_job_text(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get([detail_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert (
        "Softwareentwickler" in pos.raw_description
        or "Webanwendungen" in pos.raw_description
    )


def test_enrich_description_has_no_html_tags(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get([detail_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "<p>" not in pos.raw_description
    assert "<ul>" not in pos.raw_description


def test_enrich_description_decodes_html_entities(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get([detail_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "&auml;" not in pos.raw_description
    assert "&uuml;" not in pos.raw_description
    assert "ä" in pos.raw_description or "ü" in pos.raw_description


def test_enrich_position_references_original_stub(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get([detail_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.stub is stub


def test_enrich_source_on_stub_is_jobs_beim_staat(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get([detail_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.stub.source == "jobs-beim-staat"


# ---------------------------------------------------------------------------
# enrich — error handling
# ---------------------------------------------------------------------------


def test_enrich_raises_parser_error_on_http_failure(stub: PositionStub) -> None:
    from application_pipeline.parsers import ParserError

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("timeout")

    with JobsBeimStaatParser(_http_get=failing_get, _retries=1) as p:
        with pytest.raises(ParserError):
            p.enrich(stub)


# ---------------------------------------------------------------------------
# posted_date — threaded from listing page through to Position
# ---------------------------------------------------------------------------


def test_enrich_posted_date_set_from_vor_2_tagen(
    list_html: bytes, detail_html: bytes
) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope(), detail_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        (first_stub, *_) = list(p.discover(_query()))
        pos = p.enrich(first_stub)
    assert pos.posted_date == date.today() - timedelta(days=2)
