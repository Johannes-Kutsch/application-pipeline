from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import Parser, ParserError, ParserQuery, PositionStub
from application_pipeline.parsers.bundesagentur_api import (
    BundesagenturParser,
    parser_class,
)
from application_pipeline.parsers.http import ParserHttp
from application_pipeline.parsers.types import (
    City,
    EnrichFailedError,
    NotServedQuery,
    Remote,
)

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
            "ergebnisliste": items,
        }
    ).encode()


def _item(
    ref: str = "abc123",
    title: str = "Software Engineer",
    company: str | None = "Acme GmbH",
    city: str | None = "Hamburg",
) -> dict:
    result: dict = {
        "referenznummer": ref,
        "stellenangebotsTitel": title,
        "veroeffentlichungszeitraum": {"von": "2024-01-15"},
    }
    if company is not None:
        result["firma"] = company
    if city is not None:
        result["stellenlokationen"] = [{"adresse": {"ort": city, "plz": "20000"}}]
    return result


def _make_get(responses: list[bytes]) -> Callable[[str, float], bytes]:
    it = iter(responses)

    def http_get(url: str, timeout: float) -> bytes:
        return next(it)

    return http_get


def _make_http(responses: list[bytes], run_log: RunLog) -> ParserHttp:
    return ParserHttp(run_log=run_log, _http_get=_make_get(responses))


def _query(**kwargs: object) -> ParserQuery:
    defaults: dict = {
        "keyword": "python",
        "location": City("Hamburg"),
        "max_results": 100,
    }
    defaults.update(kwargs)
    return ParserQuery(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


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


def test_has_native_enrich_is_true() -> None:
    import application_pipeline.parsers.bundesagentur_api as mod

    assert mod.has_native_enrich is True


# ---------------------------------------------------------------------------
# Context manager / Parser protocol
# ---------------------------------------------------------------------------


def test_parser_satisfies_parser_protocol(run_log: RunLog) -> None:
    p = BundesagenturParser(run_log=run_log)
    assert isinstance(p, Parser)


def test_parser_is_usable_as_context_manager(run_log: RunLog) -> None:
    with BundesagenturParser(run_log=run_log) as p:
        assert isinstance(p, BundesagenturParser)


# ---------------------------------------------------------------------------
# discover — basic stub fields
# ---------------------------------------------------------------------------


def test_discover_yields_one_stub_per_result(run_log: RunLog) -> None:
    http = _make_http(
        [
            _search_body([_item("id1", "Dev A"), _item("id2", "Dev B")]),
            _search_body([]),
        ],
        run_log,
    )
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 2


def test_discover_stub_title_matches_api_stellenangebotsTitel(run_log: RunLog) -> None:
    http = _make_http(
        [_search_body([_item("x", "Data Scientist")]), _search_body([])], run_log
    )
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.title == "Data Scientist"


def test_discover_stub_source_is_display_name(run_log: RunLog) -> None:
    http = _make_http([_search_body([_item()]), _search_body([])], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.source == "Bundesagentur"


def test_discover_stub_company_from_firma(run_log: RunLog) -> None:
    http = _make_http(
        [_search_body([_item(company="Muster GmbH")]), _search_body([])], run_log
    )
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company == "Muster GmbH"


def test_discover_stub_location_from_stellenlokationen_first_ort(
    run_log: RunLog,
) -> None:
    http = _make_http([_search_body([_item(city="Berlin")]), _search_body([])], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location == "Berlin"


def test_discover_stub_url_is_public_job_page_url_with_raw_ref(run_log: RunLog) -> None:
    ref = "myhash"
    http = _make_http([_search_body([_item(ref)]), _search_body([])], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.url == f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}"


def test_discover_stub_company_none_when_firma_absent(run_log: RunLog) -> None:
    http = _make_http([_search_body([_item(company=None)]), _search_body([])], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company is None


def test_discover_stub_location_none_when_stellenlokationen_absent(
    run_log: RunLog,
) -> None:
    http = _make_http([_search_body([_item(city=None)]), _search_body([])], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location is None


# ---------------------------------------------------------------------------
# discover — multi-location
# ---------------------------------------------------------------------------


def test_discover_multi_location_uses_first_entry(run_log: RunLog) -> None:
    item = {
        "referenznummer": "multi1",
        "stellenangebotsTitel": "Dev",
        "veroeffentlichungszeitraum": {"von": "2024-01-15"},
        "stellenlokationen": [
            {"adresse": {"ort": "Hamburg", "plz": "20095"}},
            {"adresse": {"ort": "Berlin", "plz": "10115"}},
        ],
    }
    http = _make_http([_search_body([item]), _search_body([])], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location == "Hamburg"


# ---------------------------------------------------------------------------
# discover — missing referenznummer skipped
# ---------------------------------------------------------------------------


def test_discover_skips_item_without_referenznummer(run_log: RunLog) -> None:
    bad_item = {
        "stellenangebotsTitel": "Dev",
        "veroeffentlichungszeitraum": {"von": "2024-01-15"},
    }
    good_item = _item("good1")
    http = _make_http([_search_body([bad_item, good_item]), _search_body([])], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 1
    assert isinstance(stubs[0], PositionStub)
    assert "good1" in stubs[0].url


# ---------------------------------------------------------------------------
# discover — missing stellenangebotsTitel
# ---------------------------------------------------------------------------


def test_discover_emits_discover_page_heartbeat_per_page(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)
    page1 = _search_body([_item("id1"), _item("id2")])
    page2 = _search_body([_item("id3")])
    page3 = _search_body([])
    http = _make_http([page1, page2, page3], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        list(p.discover(_query()))
    events_rows = [
        json.loads(line)
        for line in (tmp_path / "parser" / "bundesagentur_api.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    page_rows = [row for row in events_rows if row.get("event") == "discover_page"]
    assert len(page_rows) == 3
    pages = [row["page"] for row in page_rows]
    assert pages == sorted(pages)
    assert pages[0] < pages[-1]


def test_discover_skips_item_with_missing_title_and_logs(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path)
    no_title_item = {
        "referenznummer": "notitle1",
        "veroeffentlichungszeitraum": {"von": "2024-01-15"},
    }
    good_item = _item("good1", "Backend Engineer")
    http = _make_http(
        [_search_body([no_title_item, good_item]), _search_body([])], run_log
    )
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 1
    assert isinstance(stubs[0], PositionStub)
    assert stubs[0].title == "Backend Engineer"
    events_rows = [
        json.loads(line)
        for line in (tmp_path / "parser" / "bundesagentur_api.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(row.get("event") == "missing_title" for row in events_rows)
    assert any("notitle1" in str(row) for row in events_rows)


# ---------------------------------------------------------------------------
# discover — fixture-based
# ---------------------------------------------------------------------------


def test_discover_parses_search_fixture(run_log: RunLog) -> None:
    search = _load("search.json")
    http = _make_http([search, _search_body([])], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 2
    assert isinstance(stubs[0], PositionStub)
    assert isinstance(stubs[1], PositionStub)
    assert stubs[0].title == "Software Engineer"
    assert stubs[0].source == "Bundesagentur"
    assert stubs[1].title == "Data Scientist"


# ---------------------------------------------------------------------------
# discover — pagination
# ---------------------------------------------------------------------------


def test_discover_paginates_until_empty_page(run_log: RunLog) -> None:
    page1 = _search_body([_item("id1"), _item("id2")])
    page2 = _search_body([_item("id3")])
    page3 = _search_body([])
    http = _make_http([page1, page2, page3], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 3


def test_discover_first_page_is_1_indexed(run_log: RunLog) -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query()))

    assert any("page=1" in u for u in urls)
    assert all("page=0" not in u for u in urls)


def test_discover_stops_on_null_ergebnisliste(run_log: RunLog) -> None:
    body = json.dumps({"maxErgebnisse": 0, "ergebnisliste": None}).encode()
    http = _make_http([body], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        stubs = list(p.discover(_query()))
    assert stubs == []


# ---------------------------------------------------------------------------
# discover — location slug resolution
# ---------------------------------------------------------------------------


def test_discover_resolves_location_to_slug_in_url(run_log: RunLog) -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query(location=City("Hamburg"))))

    assert any("wo=Hamburg" in u for u in urls)


def test_discover_normalizes_location_before_slug_lookup(run_log: RunLog) -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query(location=City("  MÜNCHEN  "))))

    assert any("M%C3%BCnchen" in u or "München" in u for u in urls)


def test_discover_unknown_location_yields_not_served_sentinel(
    run_log: RunLog,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def capturing_get(url: str, timeout: float) -> bytes:
        return _search_body([])

    with caplog.at_level(
        logging.INFO, logger="application_pipeline.parsers.bundesagentur_api"
    ):
        with BundesagenturParser(
            run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
        ) as p:
            stubs = list(p.discover(_query(location=City("unknown_city_xyz"))))

    assert stubs == [NotServedQuery()]
    assert not any("not_served" in r.getMessage() for r in caplog.records)


def test_discover_unknown_location_does_not_log_warning(
    run_log: RunLog,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def capturing_get(url: str, timeout: float) -> bytes:
        return _search_body([])

    with caplog.at_level(logging.WARNING):
        with BundesagenturParser(
            run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
        ) as p:
            list(p.discover(_query(location=City("unknown_city_xyz"))))

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# discover — remote (location=None uses arbeitszeit=ho)
# ---------------------------------------------------------------------------


def test_discover_location_none_uses_arbeitszeit_ho(run_log: RunLog) -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query(location=Remote())))

    assert any("arbeitszeit=ho" in u for u in urls)


def test_discover_location_none_omits_wo_param(run_log: RunLog) -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(
        run_log=run_log, _http=ParserHttp(run_log=run_log, _http_get=capturing_get)
    ) as p:
        list(p.discover(_query(location=Remote())))

    assert all("wo=" not in u for u in urls)


# ---------------------------------------------------------------------------
# discover — max_results
# ---------------------------------------------------------------------------


def test_discover_respects_max_results(run_log: RunLog) -> None:
    items = [_item(f"id{i}") for i in range(10)]
    http = _make_http([_search_body(items)], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        stubs = list(p.discover(_query(max_results=3)))
    assert len(stubs) == 3


# ---------------------------------------------------------------------------
# discover — deduplication
# ---------------------------------------------------------------------------


def test_discover_deduplicates_same_referenznummer(run_log: RunLog) -> None:
    shared = _item("same_ref", "Dev")
    page0 = _search_body([shared, shared])
    http = _make_http([page0, _search_body([])], run_log)
    with BundesagenturParser(run_log=run_log, _http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 1


# ---------------------------------------------------------------------------
# discover — error handling
# ---------------------------------------------------------------------------


def test_discover_raises_parser_error_on_http_failure(run_log: RunLog) -> None:

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with BundesagenturParser(
        run_log=run_log,
        _http=ParserHttp(run_log=run_log, _http_get=failing_get, retries=1),
    ) as p:
        with pytest.raises(ParserError):
            list(p.discover(_query()))


# ---------------------------------------------------------------------------
# enrich — native path via /jobdetails
# ---------------------------------------------------------------------------


def test_enrich_native_returns_mode_native_with_body_from_jobdetails(
    run_log: RunLog, tmp_path: Path
) -> None:
    detail = _load("detail.json")
    stub = PositionStub(
        url="https://www.arbeitsagentur.de/jobsuche/jobdetail/abc123",
        title="Software Engineer",
        source="Bundesagentur",
    )
    http = _make_http([detail], run_log)
    with BundesagenturParser(run_log=run_log, failures_dir=tmp_path, _http=http) as p:
        result = p.enrich(stub)
    assert result.mode == "native"
    assert result.body


def test_enrich_falls_back_to_html_when_jobdetails_fails_recoverably(
    run_log: RunLog, tmp_path: Path
) -> None:
    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("connection refused")

    stub = PositionStub(
        url="https://www.arbeitsagentur.de/jobsuche/jobdetail/abc123",
        title="Software Engineer",
        source="Bundesagentur",
    )
    http = ParserHttp(run_log=run_log, _http_get=failing_get, retries=1)
    with respx.mock:
        respx.get(stub.url).mock(
            return_value=httpx.Response(
                200,
                text="<html><body><p>Fallback job description text here.</p></body></html>",
            )
        )
        with BundesagenturParser(
            run_log=run_log, failures_dir=tmp_path, _http=http
        ) as p:
            result = p.enrich(stub)
    assert result.mode == "fallback"
    assert result.body


def test_enrich_raises_enrich_failed_error_when_both_paths_fail(
    run_log: RunLog, tmp_path: Path
) -> None:
    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("connection refused")

    stub = PositionStub(
        url="https://www.arbeitsagentur.de/jobsuche/jobdetail/abc123",
        title="Software Engineer",
        source="Bundesagentur",
    )
    http = ParserHttp(run_log=run_log, _http_get=failing_get, retries=1)
    with respx.mock:
        respx.get(stub.url).mock(return_value=httpx.Response(503))
        with BundesagenturParser(
            run_log=run_log, failures_dir=tmp_path, _http=http
        ) as p:
            with pytest.raises(EnrichFailedError):
                p.enrich(stub)


def test_enrich_native_backfills_posted_date_when_stub_has_none(
    run_log: RunLog, tmp_path: Path
) -> None:
    detail = _load("detail.json")
    stub = PositionStub(
        url="https://www.arbeitsagentur.de/jobsuche/jobdetail/abc123",
        title="Software Engineer",
        source="Bundesagentur",
    )
    assert stub.posted_date is None
    http = _make_http([detail], run_log)
    with BundesagenturParser(run_log=run_log, failures_dir=tmp_path, _http=http) as p:
        result = p.enrich(stub)
    assert result.stub.posted_date == date(2024, 1, 15)
