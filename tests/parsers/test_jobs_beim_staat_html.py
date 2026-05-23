from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import Parser, ParserQuery, PositionStub
from application_pipeline.parsers import jobs_beim_staat_html as parser_module
from application_pipeline.parsers.http import ParserHttp
from application_pipeline.parsers.jobs_beim_staat_html import (
    JobsBeimStaatParser,
    _parse_posted_date,
    parser_class,
)
from application_pipeline.parsers.types import (
    City,
    NotServedQuery,
    Remote,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "jobs_beim_staat"
_TODAY = date(2026, 5, 8)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _make_get(responses: list[bytes]) -> Callable[[str, float], bytes]:
    it = iter(responses)

    def http_get(url: str, timeout: float) -> bytes:
        return next(it)

    return http_get


def _jobs_envelope(jobs_html: bytes) -> bytes:
    return json.dumps({"jobs": jobs_html.decode("utf-8"), "count": -1}).encode()


def _empty_envelope() -> bytes:
    return json.dumps({"jobs": "", "count": 0}).encode()


def _make_list_page(start_id: int, count: int) -> bytes:
    """Build a jobs HTML fragment with `count` cards whose URLs start at `start_id`."""
    cards = "".join(
        f'<div class="serp-jobcontet-cards-container-joblist jobcard" id="{start_id + i}">'
        f'<h3><a href="/stellenangebote/{start_id + i}">Job {start_id + i}</a></h3>'
        f"</div>"
        for i in range(count)
    )
    return cards.encode()


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


def test_parser_satisfies_parser_protocol(run_log: RunLog) -> None:
    p = JobsBeimStaatParser(run_log=run_log)
    assert isinstance(p, Parser)


def test_parser_is_usable_as_context_manager(run_log: RunLog) -> None:
    with JobsBeimStaatParser(run_log=run_log) as p:
        assert isinstance(p, JobsBeimStaatParser)


# ---------------------------------------------------------------------------
# discover — REST URL construction
# ---------------------------------------------------------------------------


def test_discover_url_contains_sort_radius_viewtype(
    run_log: RunLog, list_html: bytes
) -> None:
    fetched_urls: list[str] = []
    responses = iter([_jobs_envelope(list_html), _empty_envelope()])

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return next(responses)

    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query()))

    first = fetched_urls[0]
    assert "sort=date" in first
    assert "radius=20" in first
    assert "viewType=card" in first


def test_discover_url_q_is_empty_for_star_keyword(
    run_log: RunLog, list_html: bytes
) -> None:
    fetched_urls: list[str] = []
    responses = iter([_jobs_envelope(list_html), _empty_envelope()])

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return next(responses)

    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query(keyword="*")))

    first = fetched_urls[0]
    assert "q=" in first
    assert "q=%2A" not in first
    assert "q=*" not in first


def test_discover_url_place_is_homeoffice_for_remote(
    run_log: RunLog, list_html: bytes
) -> None:
    fetched_urls: list[str] = []
    responses = iter([_jobs_envelope(list_html), _empty_envelope()])

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return next(responses)

    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query(location=Remote())))

    assert any("place=homeoffice" in u for u in fetched_urls)


def test_discover_url_place_uses_normalized_city_name(
    run_log: RunLog, list_html: bytes
) -> None:
    fetched_urls: list[str] = []
    responses = iter([_jobs_envelope(list_html), _empty_envelope()])

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return next(responses)

    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query(location=City("Hamburg"))))

    assert any("place=hamburg" in u for u in fetched_urls)


# ---------------------------------------------------------------------------
# discover — basic stub fields from fixture
# ---------------------------------------------------------------------------


def test_discover_yields_21_stubs_from_list_page(
    run_log: RunLog, list_html: bytes
) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 21


def test_discover_stub_source_is_jobs_beim_staat(
    run_log: RunLog, list_html: bytes
) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.source == "jobs-beim-staat"


def test_discover_stub_title_extracted(run_log: RunLog, list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.title == "Softwareentwickler/in (m/w/d)"


def test_discover_stub_url_points_to_stellenangebote(
    run_log: RunLog, list_html: bytes
) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert "stellenangebote/1001" in stub.url


def test_discover_stub_company_extracted_from_data_attribute(
    run_log: RunLog,
    list_html: bytes,
) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company == "Hamburger IT-Serviceteam GmbH"


def test_discover_stub_location_extracted(run_log: RunLog, list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        (stub, *_) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location == "Hamburg"


# ---------------------------------------------------------------------------
# discover — pagination and stop conditions
# ---------------------------------------------------------------------------


def test_discover_stops_when_jobs_fragment_has_no_cards(
    run_log: RunLog, list_html: bytes
) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 21


def test_discover_paginates_across_multiple_pages(run_log: RunLog) -> None:
    page1 = _make_list_page(start_id=1, count=21)
    page2 = _make_list_page(start_id=22, count=21)
    get = _make_get([_jobs_envelope(page1), _jobs_envelope(page2), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 42


def test_discover_respects_max_results(run_log: RunLog, list_html: bytes) -> None:
    get = _make_get([_jobs_envelope(list_html), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        stubs = list(p.discover(_query(max_results=5)))
    assert len(stubs) == 5


def test_discover_max_results_stops_mid_page(run_log: RunLog) -> None:
    page1 = _make_list_page(start_id=1, count=21)
    page2 = _make_list_page(start_id=22, count=21)
    get = _make_get([_jobs_envelope(page1), _jobs_envelope(page2), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        stubs = list(p.discover(_query(max_results=30)))
    assert len(stubs) == 30


# ---------------------------------------------------------------------------
# discover — discover_page heartbeat
# ---------------------------------------------------------------------------


def test_discover_emits_discover_page_heartbeat_per_page(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)
    page1 = _make_list_page(start_id=1, count=21)
    page2 = _make_list_page(start_id=22, count=21)
    get = _make_get([_jobs_envelope(page1), _jobs_envelope(page2), _empty_envelope()])
    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=get)
    ) as p:
        list(p.discover(_query()))
    events_rows = [
        json.loads(line)
        for line in (tmp_path / "parser_jobs_beim_staat_html.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    page_rows = [row for row in events_rows if row.get("event") == "discover_page"]
    assert len(page_rows) == 3
    starts = [row["start"] for row in page_rows]
    assert starts == sorted(starts)
    assert starts[0] < starts[-1]


# ---------------------------------------------------------------------------
# discover — NotServed arm (empty city name normalizes to None)
# ---------------------------------------------------------------------------


def test_discover_not_served_for_empty_city_yields_sentinel(run_log: RunLog) -> None:
    def never_called(url: str, timeout: float) -> bytes:
        raise AssertionError("should not fetch")

    with JobsBeimStaatParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=never_called)
    ) as p:
        stubs = list(p.discover(_query(location=City(""))))

    assert stubs == [NotServedQuery()]


# ---------------------------------------------------------------------------
# discover — error handling
# ---------------------------------------------------------------------------


def test_discover_raises_parser_error_on_http_failure(run_log: RunLog) -> None:
    from application_pipeline.parsers import ParserError

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("connection refused")

    with JobsBeimStaatParser(
        run_log=run_log,
        _http=ParserHttp(run_log=run_log, _http_get=failing_get, retries=1),
    ) as p:
        with pytest.raises(ParserError):
            list(p.discover(_query()))
