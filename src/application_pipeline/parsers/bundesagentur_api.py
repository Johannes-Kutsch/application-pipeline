from __future__ import annotations

import base64
import dataclasses
import json
import sys
import urllib.parse
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from application_pipeline.failure_report import write_failure
from application_pipeline.parser_log import RunLog

from .errors import ParserError
from .http import ParserHttp
from .location import NotServed, RemoteWire, Resolved, resolve
from .types import (
    City,
    EnrichFailedError,
    EnrichResult,
    NotServedQuery,
    ParserQuery,
    PositionStub,
)

_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6"
_PUBLIC_JOB_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail"
_API_KEY = "jobboerse-jobsuche"
_PAGE_SIZE = 25
_DISPLAY_NAME = "Bundesagentur"

_WIRE_BY_NAME: dict[str, str] = {
    "berlin": "Berlin",
    "bonn": "Bonn",
    "bremen": "Bremen",
    "dortmund": "Dortmund",
    "dresden": "Dresden",
    "duesseldorf": "Düsseldorf",
    "düsseldorf": "Düsseldorf",
    "erfurt": "Erfurt",
    "essen": "Essen",
    "frankfurt": "Frankfurt am Main",
    "frankfurt am main": "Frankfurt am Main",
    "hamburg": "Hamburg",
    "hannover": "Hannover",
    "kiel": "Kiel",
    "köln": "Köln",
    "koeln": "Köln",
    "leipzig": "Leipzig",
    "magdeburg": "Magdeburg",
    "mainz": "Mainz",
    "münchen": "München",
    "muenchen": "München",
    "nürnberg": "Nürnberg",
    "nuernberg": "Nürnberg",
    "potsdam": "Potsdam",
    "saarbrücken": "Saarbrücken",
    "saarbruecken": "Saarbrücken",
    "schwerin": "Schwerin",
    "stuttgart": "Stuttgart",
    "wiesbaden": "Wiesbaden",
}


def serves(name: str) -> bool:
    return name in _WIRE_BY_NAME


def to_wire(name: str) -> str:
    return _WIRE_BY_NAME[name]


serves_remote: bool = True
has_native_enrich: bool = True

_DETAIL_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4"
_DETAIL_URL = f"{_DETAIL_BASE_URL}/jobdetails"


def remote_wire() -> dict[str, str]:
    # PRD #51 decision 41: use arbeitszeit=ho for homeoffice
    return {"arbeitszeit": "ho"}


def _strip_html(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)


def _backfill_posted_date(stub: PositionStub, data: dict[str, Any]) -> PositionStub:
    if stub.posted_date is not None:
        return stub
    zeitraum = data.get("veroeffentlichungszeitraum") or {}
    von: str | None = zeitraum.get("von") if isinstance(zeitraum, dict) else None
    if not von:
        return stub
    try:
        return dataclasses.replace(stub, posted_date=date.fromisoformat(von))
    except ValueError:
        return stub


class BundesagenturParser:
    def __init__(
        self,
        *,
        run_log: RunLog,
        failures_dir: Path = Path("."),
        _http: ParserHttp | None = None,
    ) -> None:
        self._run_log = run_log
        self._failures_dir = failures_dir
        self._http = (
            _http
            if _http is not None
            else ParserHttp(run_log=run_log, headers={"X-API-Key": _API_KEY})
        )

    def __enter__(self) -> "BundesagenturParser":
        self._http.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._http.__exit__(*args)

    def enrich(self, stub: PositionStub) -> EnrichResult:
        ref = stub.url.rstrip("/").split("/")[-1]
        ref_b64 = base64.b64encode(ref.encode()).decode()
        try:
            raw = self._http.get(
                f"{_DETAIL_URL}/{ref_b64}",
                error_prefix="Bundesagentur jobdetails failed",
            )
        except ParserError as exc:
            raise EnrichFailedError(str(exc)) from exc
        # HttpParserFatalError, HttpRedirectResponse propagate as parser-fatal
        data: dict[str, Any] = json.loads(raw)
        description_html: str | None = data.get("stellenangebotsBeschreibung")
        body = _strip_html(description_html) if description_html else ""
        if not body:
            empty_body_error = ValueError(
                "stellenangebotsBeschreibung missing or empty — API response may have changed shape"
            )
            path = write_failure(
                stage=f"enrich:bundesagentur_api:{stub.url}",
                error=empty_body_error,
                failures_dir=self._failures_dir,
            )
            print(
                f"bundesagentur enrich failed — failure report: {path}", file=sys.stderr
            )
            raise EnrichFailedError(str(empty_body_error)) from empty_body_error
        updated_stub = _backfill_posted_date(stub, data)
        return EnrichResult(stub=updated_stub, body=body, mode="native")

    def discover(self, query: ParserQuery) -> Iterator[PositionStub | NotServedQuery]:
        extra_params: dict[str, object]
        match resolve(query.location, sys.modules[__name__]):
            case Resolved(wire):
                extra_params = {"wo": wire}
            case RemoteWire(payload):
                extra_params = dict(payload)
            case NotServed():
                yield NotServedQuery()
                return

        page = 1
        while True:
            params: dict[str, object] = {
                "was": query.keyword,
                "page": page,
                "size": _PAGE_SIZE,
                "angebotsart": 1,
                **extra_params,
            }
            url = f"{_BASE_URL}/jobs?{urllib.parse.urlencode(params)}"
            self._run_log.event(
                "parser_bundesagentur_api",
                "discover_page",
                q=query.keyword,
                page=page,
            )
            raw = self._http.get(url, error_prefix="Bundesagentur search failed")
            data: dict[str, Any] = json.loads(raw)

            items: list[dict[str, Any]] = data.get("ergebnisliste") or []
            if not items:
                break

            for item in items:
                ref: str = item.get("referenznummer") or ""
                if not ref:
                    continue
                title: str = item.get("stellenangebotsTitel") or ""
                if not title:
                    self._run_log.event(
                        "parser_bundesagentur_api",
                        "missing_title",
                        reason="stellenangebotsTitel missing or empty",
                        item=item,
                    )
                    continue
                lokationen: list[dict[str, Any]] = item.get("stellenlokationen") or []
                first_address = lokationen[0].get("adresse") or {} if lokationen else {}
                city: str | None = first_address.get("ort") or None
                yield PositionStub(
                    url=f"{_PUBLIC_JOB_URL}/{ref}",
                    title=title,
                    source=_DISPLAY_NAME,
                    company=item.get("firma") or None,
                    location=city,
                )

            page += 1


parser_class = BundesagenturParser


if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path

    from application_pipeline.parser_log import RunLog

    keyword = sys.argv[1] if len(sys.argv) > 1 else "Python"
    location = sys.argv[2] if len(sys.argv) > 2 else "Hamburg"
    query = ParserQuery(keyword=keyword, location=City(location))
    _run_log = RunLog(Path(tempfile.mkdtemp()))
    with BundesagenturParser(run_log=_run_log) as p:
        for stub in p.discover(query):
            print(stub)
