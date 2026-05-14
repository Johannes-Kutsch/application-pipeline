from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import application_pipeline.parser_log as parser_log
from application_pipeline.parsers import Parser, ParserQuery, PositionStub
from application_pipeline.parsers.types import (
    City,
    ExternalRedirect,
    NotServedQuery,
    Position,
    Remote,
)
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
def wrapper_html() -> bytes:
    return _load("wrapper.html")


@pytest.fixture
def iframe_target_html() -> bytes:
    return _load("iframe_target.html")


@pytest.fixture
def stub() -> PositionStub:
    return PositionStub(
        url="https://www.jobs-beim-staat.de/jobangebote/1965251471",
        title="Softwareentwickler/in (m/w/d)",
        source="jobs-beim-staat",
    )


# ---------------------------------------------------------------------------
# _parse_posted_date — all patterns
# ---------------------------------------------------------------------------


def test_parse_posted_date_heute_returns_today() -> None:
    result, warning = _parse_posted_date("heute", _TODAY)
    assert result == _TODAY
    assert warning is None


def test_parse_posted_date_gestern_returns_yesterday() -> None:
    result, warning = _parse_posted_date("gestern", _TODAY)
    assert result == date(2026, 5, 7)
    assert warning is None


def test_parse_posted_date_vor_n_tagen_subtracts_days() -> None:
    result, warning = _parse_posted_date("vor 3 Tagen", _TODAY)
    assert result == date(2026, 5, 5)
    assert warning is None


def test_parse_posted_date_vor_1_tag_singular() -> None:
    result, warning = _parse_posted_date("vor 1 Tag", _TODAY)
    assert result == date(2026, 5, 7)
    assert warning is None


def test_parse_posted_date_vor_n_wochen_subtracts_weeks() -> None:
    result, warning = _parse_posted_date("vor 2 Wochen", _TODAY)
    assert result == date(2026, 4, 24)
    assert warning is None


def test_parse_posted_date_vor_1_woche_singular() -> None:
    result, warning = _parse_posted_date("vor 1 Woche", _TODAY)
    assert result == date(2026, 5, 1)
    assert warning is None


def test_parse_posted_date_dmy_format() -> None:
    result, warning = _parse_posted_date("15.04.2026", _TODAY)
    assert result == date(2026, 4, 15)
    assert warning is None


def test_parse_posted_date_unparseable_returns_none_and_warning() -> None:
    result, warning = _parse_posted_date("irgendwann mal", _TODAY)
    assert result is None
    assert warning is not None
    assert "unparseable_date" in warning
    assert "irgendwann mal" in warning


def test_parse_posted_date_case_insensitive_heute() -> None:
    result, warning = _parse_posted_date("Heute", _TODAY)
    assert result == _TODAY
    assert warning is None


def test_parse_posted_date_case_insensitive_gestern() -> None:
    result, warning = _parse_posted_date("Gestern", _TODAY)
    assert result == date(2026, 5, 7)
    assert warning is None


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
    assert isinstance(stub, PositionStub)
    assert stub.source == "jobs-beim-staat"


def test_discover_stub_title_extracted(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.title == "Softwareentwickler/in (m/w/d)"


def test_discover_stub_url_points_to_stellenangebote(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert "stellenangebote/1001" in stub.url


def test_discover_stub_company_extracted_from_data_attribute(
    list_html: bytes,
) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company == "Hamburger IT-Serviceteam GmbH"


def test_discover_stub_location_extracted(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location == "Hamburg"


def test_discover_stub_language_is_de(list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
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
# discover — discover_page heartbeat
# ---------------------------------------------------------------------------


def test_discover_emits_discover_page_heartbeat_per_page(
    tmp_path: Path, list_html: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parser_log, "_logs_dir", tmp_path)
    get = _make_get(
        [_jobs_envelope(list_html), _jobs_envelope(list_html), _empty_envelope()]
    )
    with JobsBeimStaatParser(_http_get=get) as p:
        list(p.discover(_query()))
    log_content = (tmp_path / "jobs_beim_staat_html.log").read_text(encoding="utf-8")
    lines = [ln for ln in log_content.splitlines() if "discover_page" in ln]
    assert len(lines) == 3
    starts = [int(ln.split("start=")[1].split()[0]) for ln in lines]
    assert starts == sorted(starts)
    assert starts[0] < starts[-1]


# ---------------------------------------------------------------------------
# discover — NotServed arm (empty city name normalizes to None)
# ---------------------------------------------------------------------------


def test_discover_not_served_for_empty_city_yields_sentinel() -> None:
    def never_called(url: str, timeout: float) -> bytes:
        raise AssertionError("should not fetch")

    with JobsBeimStaatParser(_http_get=never_called) as p:
        stubs = list(p.discover(_query(location=City(""))))

    assert stubs == [NotServedQuery()]


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
# enrich — two-fetch flow
# ---------------------------------------------------------------------------


def test_enrich_fetches_wrapper_then_on_domain_iframe_target(
    stub: PositionStub, wrapper_html: bytes, iframe_target_html: bytes
) -> None:
    fetched_urls: list[str] = []
    responses = iter([wrapper_html, iframe_target_html])

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return next(responses)

    with JobsBeimStaatParser(_http_get=capturing_get) as p:
        p.enrich(stub)

    assert fetched_urls[0] == stub.url
    assert (
        fetched_urls[1]
        == "https://www.jobs-beim-staat.de/stellenanzeigen-details/?id=22630"
    )
    assert "jdn.jobs-beim-staat.de" not in fetched_urls[1]


def test_enrich_returns_position_with_raw_description(
    stub: PositionStub, wrapper_html: bytes, iframe_target_html: bytes
) -> None:
    get = _make_get([wrapper_html, iframe_target_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.raw_description != ""


def test_enrich_description_contains_job_text(
    stub: PositionStub, wrapper_html: bytes, iframe_target_html: bytes
) -> None:
    get = _make_get([wrapper_html, iframe_target_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert (
        "Softwareentwickler" in pos.raw_description
        or "Webanwendungen" in pos.raw_description
    )


def test_enrich_description_has_no_html_tags(
    stub: PositionStub, wrapper_html: bytes, iframe_target_html: bytes
) -> None:
    get = _make_get([wrapper_html, iframe_target_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert "<p>" not in pos.raw_description
    assert "<ul>" not in pos.raw_description


def test_enrich_position_url_remains_wrapper_url(
    stub: PositionStub, wrapper_html: bytes, iframe_target_html: bytes
) -> None:
    get = _make_get([wrapper_html, iframe_target_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.stub.url == stub.url


def test_enrich_position_references_original_stub(
    stub: PositionStub, wrapper_html: bytes, iframe_target_html: bytes
) -> None:
    get = _make_get([wrapper_html, iframe_target_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.stub is stub


# ---------------------------------------------------------------------------
# enrich — error handling
# ---------------------------------------------------------------------------


def test_enrich_raises_parser_error_on_wrapper_http_failure(stub: PositionStub) -> None:
    from application_pipeline.parsers import ParserError

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("timeout")

    with JobsBeimStaatParser(_http_get=failing_get, _retries=1) as p:
        with pytest.raises(ParserError):
            p.enrich(stub)


def test_enrich_raises_parser_error_on_iframe_target_http_failure(
    stub: PositionStub, wrapper_html: bytes
) -> None:
    from application_pipeline.parsers import ParserError

    call_count = 0

    def get(url: str, timeout: float) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return wrapper_html
        raise OSError("timeout")

    with JobsBeimStaatParser(_http_get=get, _retries=1) as p:
        with pytest.raises(ParserError):
            p.enrich(stub)


def test_enrich_raises_parser_error_on_wrapper_with_no_iframe_target(
    stub: PositionStub,
) -> None:
    from application_pipeline.parsers import ParserError

    empty_wrapper = b"<html><body><p>No iframe here</p></body></html>"
    get = _make_get([empty_wrapper])
    with JobsBeimStaatParser(_http_get=get) as p:
        with pytest.raises(ParserError):
            p.enrich(stub)


# ---------------------------------------------------------------------------
# posted_date — threaded from listing page through to Position
# ---------------------------------------------------------------------------


def test_enrich_posted_date_set_from_vor_2_tagen(
    list_html: bytes, wrapper_html: bytes, iframe_target_html: bytes
) -> None:
    get = _make_get(
        [_jobs_envelope(list_html), _empty_envelope(), wrapper_html, iframe_target_html]
    )
    with JobsBeimStaatParser(_http_get=get) as p:
        (first_stub, *_) = list(p.discover(_query()))
        assert isinstance(first_stub, PositionStub)
        pos = p.enrich(first_stub)
    assert isinstance(pos, Position)
    assert pos.posted_date == date.today() - timedelta(days=2)


# ---------------------------------------------------------------------------
# enrich — external redirect detection
# ---------------------------------------------------------------------------

_OUTBOUND_WRAPPER = (
    b"<html><body>"
    b'<a href="https://go.opportuno.de/job/123">Apply here</a>'
    b"</body></html>"
)


def test_enrich_emits_external_redirect_for_outbound_link(stub: PositionStub) -> None:
    get = _make_get([_OUTBOUND_WRAPPER])
    with JobsBeimStaatParser(_http_get=get) as p:
        result = p.enrich(stub)
    assert isinstance(result, ExternalRedirect)
    assert result.outbound_url == "https://go.opportuno.de/job/123"
    assert result.stub is stub


def test_enrich_external_redirect_outbound_url_matches_href(stub: PositionStub) -> None:
    wrapper = (
        b"<html><body>"
        b'<a href="https://external.example.com/jobs/456">Apply</a>'
        b"</body></html>"
    )
    get = _make_get([wrapper])
    with JobsBeimStaatParser(_http_get=get) as p:
        result = p.enrich(stub)
    assert isinstance(result, ExternalRedirect)
    assert result.outbound_url == "https://external.example.com/jobs/456"


def test_enrich_raises_parser_error_with_no_iframe_and_no_outbound(
    stub: PositionStub,
) -> None:
    from application_pipeline.parsers import ParserError

    no_link_wrapper = b"<html><body><p>No links here</p></body></html>"
    get = _make_get([no_link_wrapper])
    with JobsBeimStaatParser(_http_get=get) as p:
        with pytest.raises(ParserError):
            p.enrich(stub)


def test_enrich_iframe_wins_over_stray_outbound_link(
    stub: PositionStub, iframe_target_html: bytes
) -> None:
    wrapper_with_both = (
        b"<html><body>"
        b'<input name="raw-url" value="/stellenanzeigen-details/?id=22630" />'
        b'<a href="https://go.opportuno.de/job/999">External link</a>'
        b"</body></html>"
    )
    get = _make_get([wrapper_with_both, iframe_target_html])
    with JobsBeimStaatParser(_http_get=get) as p:
        result = p.enrich(stub)
    assert isinstance(result, Position)


def test_enrich_external_redirect_returns_external_redirect(
    stub: PositionStub,
) -> None:
    get = _make_get([_OUTBOUND_WRAPPER])
    with JobsBeimStaatParser(_http_get=get) as p:
        result = p.enrich(stub)
    assert isinstance(result, ExternalRedirect)
    assert "go.opportuno.de/job/123" in result.outbound_url
