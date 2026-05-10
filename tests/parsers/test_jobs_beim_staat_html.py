from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from application_pipeline.parsers import Parser, ParserQuery, PositionStub
from application_pipeline.parsers.http import HttpGet
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


def _make_get(responses: dict[str, bytes]) -> HttpGet:
    def http_get(url: str, timeout: float) -> bytes:
        for key, body in responses.items():
            if key in url:
                return body
        raise OSError(f"unexpected URL: {url}")

    return http_get


def _query(**kwargs: object) -> ParserQuery:
    defaults: dict = {"keyword": "python", "location": "hamburg", "max_results": 100}
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
# discover — basic stub fields from fixture
# ---------------------------------------------------------------------------


def test_discover_yields_stubs_from_list_page(list_html: bytes) -> None:
    get = _make_get({"jobs/hamburg": list_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 3


def test_discover_stub_source_is_jobs_beim_staat(list_html: bytes) -> None:
    get = _make_get({"jobs/hamburg": list_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert stub.source == "jobs-beim-staat"


def test_discover_stub_title_extracted(list_html: bytes) -> None:
    get = _make_get({"jobs/hamburg": list_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert stub.title == "Softwareentwickler/in (m/w/d)"


def test_discover_stub_url_points_to_stellenangebote(list_html: bytes) -> None:
    get = _make_get({"jobs/hamburg": list_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert "stellenangebote/1001" in stub.url


def test_discover_stub_company_extracted(list_html: bytes) -> None:
    get = _make_get({"jobs/hamburg": list_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert stub.company == "Hamburger IT-Serviceteam GmbH"


def test_discover_stub_location_extracted(list_html: bytes) -> None:
    get = _make_get({"jobs/hamburg": list_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert stub.location == "Hamburg"


def test_discover_stub_language_is_de(list_html: bytes) -> None:
    get = _make_get({"jobs/hamburg": list_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        (stub, *_) = list(p.discover(_query()))
    assert stub.language == "de"


# ---------------------------------------------------------------------------
# discover — max_results cap
# ---------------------------------------------------------------------------


def test_discover_respects_max_results(list_html: bytes) -> None:
    get = _make_get({"jobs/hamburg": list_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        stubs = list(p.discover(_query(max_results=2)))
    assert len(stubs) == 2


# ---------------------------------------------------------------------------
# discover — location routing
# ---------------------------------------------------------------------------


def test_discover_uses_homeoffice_slug_when_location_is_homeoffice(
    list_html: bytes,
) -> None:
    fetched_urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return list_html

    with JobsBeimStaatParser(_http_get=capturing_get) as p:
        list(p.discover(_query(location="homeoffice")))

    assert any("homeoffice" in u for u in fetched_urls)


def test_discover_unknown_location_logs_warning_and_yields_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    def never_called(url: str, timeout: float) -> bytes:
        raise AssertionError("should not fetch")

    with caplog.at_level(logging.WARNING):
        with JobsBeimStaatParser(_http_get=never_called) as p:
            stubs = list(p.discover(_query(location="UnknownCity99")))

    assert stubs == []
    assert "unknown_location" in caplog.text


def test_discover_yields_nothing_when_location_is_none() -> None:
    def never_called(url: str, timeout: float) -> bytes:
        raise AssertionError("should not fetch")

    with JobsBeimStaatParser(_http_get=never_called) as p:
        stubs = list(p.discover(_query(location=None)))

    assert stubs == []


def test_discover_fetches_correct_slug_for_location(list_html: bytes) -> None:
    fetched_urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return list_html

    with JobsBeimStaatParser(_http_get=capturing_get) as p:
        list(p.discover(_query(location="hamburg")))

    assert any("jobs/hamburg" in u for u in fetched_urls)


def test_discover_normalizes_location_case(list_html: bytes) -> None:
    fetched_urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        fetched_urls.append(url)
        return list_html

    with JobsBeimStaatParser(_http_get=capturing_get) as p:
        list(p.discover(_query(location="Hamburg")))

    assert any("jobs/hamburg" in u for u in fetched_urls)


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
    get = _make_get({"stellenangebote": detail_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.raw_description != ""


def test_enrich_description_contains_job_text(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get({"stellenangebote": detail_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert (
        "Softwareentwickler" in pos.raw_description
        or "Webanwendungen" in pos.raw_description
    )


def test_enrich_description_has_no_html_tags(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get({"stellenangebote": detail_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "<p>" not in pos.raw_description
    assert "<ul>" not in pos.raw_description


def test_enrich_description_decodes_html_entities(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get({"stellenangebote": detail_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert "&auml;" not in pos.raw_description
    assert "&uuml;" not in pos.raw_description
    assert "ä" in pos.raw_description or "ü" in pos.raw_description


def test_enrich_position_references_original_stub(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get({"stellenangebote": detail_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        pos = p.enrich(stub)
    assert pos.stub is stub


def test_enrich_source_on_stub_is_jobs_beim_staat(
    stub: PositionStub, detail_html: bytes
) -> None:
    get = _make_get({"stellenangebote": detail_html})
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
    get = _make_get({"jobs/hamburg": list_html, "stellenangebote": detail_html})
    with JobsBeimStaatParser(_http_get=get) as p:
        (first_stub, *_) = list(p.discover(_query()))
        pos = p.enrich(first_stub)
    assert pos.posted_date == date.today() - timedelta(days=2)
