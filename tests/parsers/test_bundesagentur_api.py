from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

import application_pipeline.parser_log as parser_log
from application_pipeline.parsers import Parser, ParserQuery, PositionStub
from application_pipeline.parsers.bundesagentur_api import (
    BundesagenturParser,
    parser_class,
)
from application_pipeline.parsers.http import ParserHttp
from application_pipeline.parsers.types import (
    City,
    ExternalRedirect,
    NotServedQuery,
    Position,
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


def _detail_body(
    ref: str = "abc123",
    title: str = "Software Engineer",
    description: str = "",
    **extra: object,
) -> bytes:
    return json.dumps(
        {
            "referenznummer": ref,
            "stellenangebotsTitel": title,
            "stellenangebotsBeschreibung": description,
            **extra,
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


def _make_http(responses: list[bytes]) -> ParserHttp:
    return ParserHttp(_http_get=_make_get(responses))


def _query(**kwargs: object) -> ParserQuery:
    defaults: dict = {
        "keyword": "python",
        "location": City("Hamburg"),
        "max_results": 100,
    }
    defaults.update(kwargs)
    return ParserQuery(**defaults)  # type: ignore[arg-type]


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


# ---------------------------------------------------------------------------
# Context manager / Parser protocol
# ---------------------------------------------------------------------------


def test_parser_satisfies_parser_protocol() -> None:
    p = BundesagenturParser()
    assert isinstance(p, Parser)


def test_parser_is_usable_as_context_manager() -> None:
    with BundesagenturParser() as p:
        assert isinstance(p, BundesagenturParser)


# ---------------------------------------------------------------------------
# discover — basic stub fields
# ---------------------------------------------------------------------------


def test_discover_yields_one_stub_per_result() -> None:
    http = _make_http(
        [
            _search_body([_item("id1", "Dev A"), _item("id2", "Dev B")]),
            _search_body([]),
        ]
    )
    with BundesagenturParser(_http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 2


def test_discover_stub_title_matches_api_stellenangebotsTitel() -> None:
    http = _make_http([_search_body([_item("x", "Data Scientist")]), _search_body([])])
    with BundesagenturParser(_http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.title == "Data Scientist"


def test_discover_stub_source_is_display_name() -> None:
    http = _make_http([_search_body([_item()]), _search_body([])])
    with BundesagenturParser(_http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.source == "Bundesagentur"


def test_discover_stub_company_from_firma() -> None:
    http = _make_http([_search_body([_item(company="Muster GmbH")]), _search_body([])])
    with BundesagenturParser(_http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company == "Muster GmbH"


def test_discover_stub_location_from_stellenlokationen_first_ort() -> None:
    http = _make_http([_search_body([_item(city="Berlin")]), _search_body([])])
    with BundesagenturParser(_http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location == "Berlin"


def test_discover_stub_url_is_public_job_page_url_with_raw_ref() -> None:
    ref = "myhash"
    http = _make_http([_search_body([_item(ref)]), _search_body([])])
    with BundesagenturParser(_http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.url == f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}"


def test_discover_stub_company_none_when_firma_absent() -> None:
    http = _make_http([_search_body([_item(company=None)]), _search_body([])])
    with BundesagenturParser(_http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.company is None


def test_discover_stub_location_none_when_stellenlokationen_absent() -> None:
    http = _make_http([_search_body([_item(city=None)]), _search_body([])])
    with BundesagenturParser(_http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location is None


# ---------------------------------------------------------------------------
# discover — multi-location
# ---------------------------------------------------------------------------


def test_discover_multi_location_uses_first_entry() -> None:
    item = {
        "referenznummer": "multi1",
        "stellenangebotsTitel": "Dev",
        "veroeffentlichungszeitraum": {"von": "2024-01-15"},
        "stellenlokationen": [
            {"adresse": {"ort": "Hamburg", "plz": "20095"}},
            {"adresse": {"ort": "Berlin", "plz": "10115"}},
        ],
    }
    http = _make_http([_search_body([item]), _search_body([])])
    with BundesagenturParser(_http=http) as p:
        (stub,) = list(p.discover(_query()))
    assert isinstance(stub, PositionStub)
    assert stub.location == "Hamburg"


# ---------------------------------------------------------------------------
# discover — missing referenznummer skipped
# ---------------------------------------------------------------------------


def test_discover_skips_item_without_referenznummer() -> None:
    bad_item = {
        "stellenangebotsTitel": "Dev",
        "veroeffentlichungszeitraum": {"von": "2024-01-15"},
    }
    good_item = _item("good1")
    http = _make_http([_search_body([bad_item, good_item]), _search_body([])])
    with BundesagenturParser(_http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 1
    assert isinstance(stubs[0], PositionStub)
    assert "good1" in stubs[0].url


# ---------------------------------------------------------------------------
# discover — missing stellenangebotsTitel
# ---------------------------------------------------------------------------


def test_discover_emits_discover_page_heartbeat_per_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parser_log, "_logs_dir", tmp_path)
    page1 = _search_body([_item("id1"), _item("id2")])
    page2 = _search_body([_item("id3")])
    page3 = _search_body([])
    http = _make_http([page1, page2, page3])
    with BundesagenturParser(_http=http) as p:
        list(p.discover(_query()))
    events_rows = [
        json.loads(line)
        for line in (tmp_path / "parser_bundesagentur_api.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    page_rows = [row for row in events_rows if row.get("event") == "discover_page"]
    assert len(page_rows) == 3
    pages = [row["page"] for row in page_rows]
    assert pages == sorted(pages)
    assert pages[0] < pages[-1]


def test_discover_skips_item_with_missing_title_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parser_log, "_logs_dir", tmp_path)
    no_title_item = {
        "referenznummer": "notitle1",
        "veroeffentlichungszeitraum": {"von": "2024-01-15"},
    }
    good_item = _item("good1", "Backend Engineer")
    http = _make_http([_search_body([no_title_item, good_item]), _search_body([])])
    with BundesagenturParser(_http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 1
    assert isinstance(stubs[0], PositionStub)
    assert stubs[0].title == "Backend Engineer"
    events_rows = [
        json.loads(line)
        for line in (tmp_path / "parser_bundesagentur_api.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(row.get("event") == "missing_title" for row in events_rows)
    assert any("notitle1" in str(row) for row in events_rows)


# ---------------------------------------------------------------------------
# discover — fixture-based
# ---------------------------------------------------------------------------


def test_discover_parses_search_fixture() -> None:
    search = _load("search.json")
    http = _make_http([search, _search_body([])])
    with BundesagenturParser(_http=http) as p:
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


def test_discover_paginates_until_empty_page() -> None:
    page1 = _search_body([_item("id1"), _item("id2")])
    page2 = _search_body([_item("id3")])
    page3 = _search_body([])
    http = _make_http([page1, page2, page3])
    with BundesagenturParser(_http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 3


def test_discover_first_page_is_1_indexed() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(_http=ParserHttp(_http_get=capturing_get)) as p:
        list(p.discover(_query()))

    assert any("page=1" in u for u in urls)
    assert all("page=0" not in u for u in urls)


def test_discover_stops_on_null_ergebnisliste() -> None:
    body = json.dumps({"maxErgebnisse": 0, "ergebnisliste": None}).encode()
    http = _make_http([body])
    with BundesagenturParser(_http=http) as p:
        stubs = list(p.discover(_query()))
    assert stubs == []


# ---------------------------------------------------------------------------
# discover — location slug resolution
# ---------------------------------------------------------------------------


def test_discover_resolves_location_to_slug_in_url() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(_http=ParserHttp(_http_get=capturing_get)) as p:
        list(p.discover(_query(location=City("Hamburg"))))

    assert any("wo=Hamburg" in u for u in urls)


def test_discover_normalizes_location_before_slug_lookup() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(_http=ParserHttp(_http_get=capturing_get)) as p:
        list(p.discover(_query(location=City("  MÜNCHEN  "))))

    assert any("M%C3%BCnchen" in u or "München" in u for u in urls)


def test_discover_unknown_location_yields_not_served_sentinel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def capturing_get(url: str, timeout: float) -> bytes:
        return _search_body([])

    with caplog.at_level(
        logging.INFO, logger="application_pipeline.parsers.bundesagentur_api"
    ):
        with BundesagenturParser(_http=ParserHttp(_http_get=capturing_get)) as p:
            stubs = list(p.discover(_query(location=City("unknown_city_xyz"))))

    assert stubs == [NotServedQuery()]
    assert not any("not_served" in r.getMessage() for r in caplog.records)


def test_discover_unknown_location_does_not_log_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def capturing_get(url: str, timeout: float) -> bytes:
        return _search_body([])

    with caplog.at_level(logging.WARNING):
        with BundesagenturParser(_http=ParserHttp(_http_get=capturing_get)) as p:
            list(p.discover(_query(location=City("unknown_city_xyz"))))

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# discover — remote (location=None uses arbeitszeit=ho)
# ---------------------------------------------------------------------------


def test_discover_location_none_uses_arbeitszeit_ho() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(_http=ParserHttp(_http_get=capturing_get)) as p:
        list(p.discover(_query(location=Remote())))

    assert any("arbeitszeit=ho" in u for u in urls)


def test_discover_location_none_omits_wo_param() -> None:
    urls: list[str] = []

    def capturing_get(url: str, timeout: float) -> bytes:
        urls.append(url)
        return _search_body([])

    with BundesagenturParser(_http=ParserHttp(_http_get=capturing_get)) as p:
        list(p.discover(_query(location=Remote())))

    assert all("wo=" not in u for u in urls)


# ---------------------------------------------------------------------------
# discover — max_results
# ---------------------------------------------------------------------------


def test_discover_respects_max_results() -> None:
    items = [_item(f"id{i}") for i in range(10)]
    http = _make_http([_search_body(items)])
    with BundesagenturParser(_http=http) as p:
        stubs = list(p.discover(_query(max_results=3)))
    assert len(stubs) == 3


# ---------------------------------------------------------------------------
# discover — deduplication
# ---------------------------------------------------------------------------


def test_discover_deduplicates_same_referenznummer() -> None:
    shared = _item("same_ref", "Dev")
    page0 = _search_body([shared, shared])
    http = _make_http([page0, _search_body([])])
    with BundesagenturParser(_http=http) as p:
        stubs = list(p.discover(_query()))
    assert len(stubs) == 1


# ---------------------------------------------------------------------------
# discover — error handling
# ---------------------------------------------------------------------------


def test_discover_raises_parser_error_on_http_failure() -> None:
    from application_pipeline.parsers import ParserError

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with BundesagenturParser(_http=ParserHttp(_http_get=failing_get, retries=1)) as p:
        with pytest.raises(ParserError):
            list(p.discover(_query()))


# ---------------------------------------------------------------------------
# enrich — fixture-based
# ---------------------------------------------------------------------------


def test_enrich_parses_detail_fixture(stub: PositionStub) -> None:
    http = _make_http([_load("detail.json")])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert "Software Engineer" in pos.raw_description or pos.raw_description != ""
    assert pos.contract_type == "permanent"
    assert pos.employment_type == "full-time"
    assert pos.posted_date == date(2024, 1, 15)


# ---------------------------------------------------------------------------
# enrich — raw description
# ---------------------------------------------------------------------------


def test_enrich_returns_position_with_raw_description(stub: PositionStub) -> None:
    http = _make_http([_detail_body(description="We are hiring.")])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.raw_description == "We are hiring."


def test_enrich_strips_html_tags_from_description(stub: PositionStub) -> None:
    http = _make_http([_detail_body(description="<p>Hello</p><p>World</p>")])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert "<p>" not in pos.raw_description
    assert "Hello" in pos.raw_description
    assert "World" in pos.raw_description


def test_enrich_decodes_html_entities_in_description(stub: PositionStub) -> None:
    http = _make_http([_detail_body(description="Geh&auml;lter &amp; Benefits")])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert "Gehälter" in pos.raw_description
    assert "&amp;" not in pos.raw_description


def test_enrich_empty_description_when_field_absent(stub: PositionStub) -> None:
    body = json.dumps({"referenznummer": "abc", "stellenangebotsTitel": "Dev"}).encode()
    http = _make_http([body])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.raw_description == ""


# ---------------------------------------------------------------------------
# enrich — contract_type (vertragsdauer)
# ---------------------------------------------------------------------------


def test_enrich_maps_unbefristet_to_permanent(stub: PositionStub) -> None:
    http = _make_http([_detail_body(vertragsdauer="UNBEFRISTET")])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.contract_type == "permanent"


def test_enrich_maps_befristet_to_fixed_term(stub: PositionStub) -> None:
    http = _make_http([_detail_body(vertragsdauer="BEFRISTET")])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.contract_type == "fixed-term"


def test_enrich_contract_type_none_when_vertragsdauer_absent(
    stub: PositionStub,
) -> None:
    http = _make_http([_detail_body()])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.contract_type is None


def test_enrich_contract_type_none_for_unknown_vertragsdauer(
    stub: PositionStub,
) -> None:
    http = _make_http([_detail_body(vertragsdauer="KEINE_ANGABE")])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.contract_type is None


# ---------------------------------------------------------------------------
# enrich — employment_type (boolean flags)
# ---------------------------------------------------------------------------


def test_enrich_arbeitszeitvollzeit_true_maps_to_full_time(stub: PositionStub) -> None:
    http = _make_http([_detail_body(arbeitszeitVollzeit=True)])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.employment_type == "full-time"


def test_enrich_vollzeit_wins_over_teilzeit_flags(stub: PositionStub) -> None:
    http = _make_http(
        [_detail_body(arbeitszeitVollzeit=True, arbeitszeitTeilzeitVormittag=True)]
    )
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.employment_type == "full-time"


def test_enrich_teilzeit_flag_maps_to_part_time(stub: PositionStub) -> None:
    http = _make_http(
        [_detail_body(arbeitszeitVollzeit=False, arbeitszeitTeilzeitVormittag=True)]
    )
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.employment_type == "part-time"


def test_enrich_employment_type_none_when_all_flags_false(stub: PositionStub) -> None:
    http = _make_http(
        [_detail_body(arbeitszeitVollzeit=False, arbeitszeitTeilzeitVormittag=False)]
    )
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.employment_type is None


def test_enrich_employment_type_none_when_flags_absent(
    stub: PositionStub,
) -> None:
    http = _make_http([_detail_body()])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.employment_type is None


# ---------------------------------------------------------------------------
# enrich — dates
# ---------------------------------------------------------------------------


def test_enrich_parses_posted_date_from_veroeffentlichungszeitraum(
    stub: PositionStub,
) -> None:
    http = _make_http([_detail_body(veroeffentlichungszeitraum={"von": "2024-03-15"})])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.posted_date == date(2024, 3, 15)


def test_enrich_parses_deadline(stub: PositionStub) -> None:
    http = _make_http([_detail_body(bewerbungsschluss="2024-04-30")])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.deadline == date(2024, 4, 30)


def test_enrich_posted_date_none_when_field_absent(stub: PositionStub) -> None:
    http = _make_http([_detail_body()])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert isinstance(pos, Position)
    assert pos.posted_date is None


# ---------------------------------------------------------------------------
# enrich — error handling
# ---------------------------------------------------------------------------


def test_enrich_raises_parser_error_on_http_failure() -> None:
    from application_pipeline.parsers import ParserError

    s = PositionStub(
        url="https://example.com/jobdetails/abc",
        title="Dev",
        source="Bundesagentur",
    )

    def failing_get(url: str, timeout: float) -> bytes:
        raise OSError("refused")

    with BundesagenturParser(_http=ParserHttp(_http_get=failing_get, retries=1)) as p:
        with pytest.raises(ParserError):
            p.enrich(s)


# ---------------------------------------------------------------------------
# enrich — stub reference preserved
# ---------------------------------------------------------------------------


def test_enrich_position_references_original_stub(stub: PositionStub) -> None:
    http = _make_http([_detail_body()])
    with BundesagenturParser(_http=http) as p:
        pos = p.enrich(stub)
    assert pos.stub is stub


# ---------------------------------------------------------------------------
# enrich — externeURL detection (external_redirect event)
# ---------------------------------------------------------------------------


def test_enrich_externe_url_empty_body_returns_external_redirect(
    stub: PositionStub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parser_log, "_logs_dir", tmp_path)
    http = _make_http([_detail_body(externeURL="https://jobs.example.com/123")])
    with BundesagenturParser(_http=http) as p:
        result = p.enrich(stub)
    assert isinstance(result, ExternalRedirect)
    assert result.stub is stub
    assert result.outbound_url == "https://jobs.example.com/123"


def test_enrich_externe_url_empty_body_emits_external_redirect_event_skipped_true(
    stub: PositionStub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parser_log, "_logs_dir", tmp_path)
    http = _make_http([_detail_body(externeURL="https://jobs.example.com/123")])
    with BundesagenturParser(_http=http) as p:
        p.enrich(stub)
    events = [
        json.loads(line)
        for line in (tmp_path / "parser_bundesagentur_api.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    redirect_events = [e for e in events if e.get("event") == "external_redirect"]
    assert len(redirect_events) == 1
    assert redirect_events[0]["stub_url"] == stub.url
    assert redirect_events[0]["outbound"] == "https://jobs.example.com/123"
    assert redirect_events[0]["skipped"] is True


def test_enrich_externe_url_html_only_body_treated_as_empty(
    stub: PositionStub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parser_log, "_logs_dir", tmp_path)
    http = _make_http(
        [
            _detail_body(
                description="<p>  </p>", externeURL="https://jobs.example.com/456"
            )
        ]
    )
    with BundesagenturParser(_http=http) as p:
        result = p.enrich(stub)
    assert isinstance(result, ExternalRedirect)
    assert result.outbound_url == "https://jobs.example.com/456"


def test_enrich_externe_url_nonempty_body_returns_position(
    stub: PositionStub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parser_log, "_logs_dir", tmp_path)
    http = _make_http(
        [
            _detail_body(
                description="Wir bieten tolle Jobs.",
                externeURL="https://jobs.example.com/789",
            )
        ]
    )
    with BundesagenturParser(_http=http) as p:
        result = p.enrich(stub)
    assert isinstance(result, Position)
    assert result.raw_description == "Wir bieten tolle Jobs."


def test_enrich_externe_url_nonempty_body_emits_external_redirect_event_skipped_false(
    stub: PositionStub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parser_log, "_logs_dir", tmp_path)
    http = _make_http(
        [
            _detail_body(
                description="Wir bieten tolle Jobs.",
                externeURL="https://jobs.example.com/789",
            )
        ]
    )
    with BundesagenturParser(_http=http) as p:
        p.enrich(stub)
    events = [
        json.loads(line)
        for line in (tmp_path / "parser_bundesagentur_api.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    redirect_events = [e for e in events if e.get("event") == "external_redirect"]
    assert len(redirect_events) == 1
    assert redirect_events[0]["stub_url"] == stub.url
    assert redirect_events[0]["outbound"] == "https://jobs.example.com/789"
    assert redirect_events[0]["skipped"] is False


def test_enrich_no_externe_url_emits_no_external_redirect_event(
    stub: PositionStub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parser_log, "_logs_dir", tmp_path)
    http = _make_http([_detail_body(description="Normal description.")])
    with BundesagenturParser(_http=http) as p:
        result = p.enrich(stub)
    assert isinstance(result, Position)
    events_file = tmp_path / "parser_bundesagentur_api.events.jsonl"
    if events_file.exists():
        events = [
            json.loads(line)
            for line in events_file.read_text(encoding="utf-8").splitlines()
        ]
        assert not any(e.get("event") == "external_redirect" for e in events)
