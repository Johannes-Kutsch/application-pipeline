from __future__ import annotations

import base64
import json
import sys
import urllib.parse
from collections.abc import Iterator
from typing import Any, Literal
from urllib.parse import urlparse

from application_pipeline.http import HttpRedirectResponse
from application_pipeline.parser_log import RunLog

from ._text import parse_iso_date, strip_html
from .errors import ParserError
from .http import ParserHttp
from .location import NotServed, RemoteWire, Resolved, resolve
from .types import (
    City,
    ExternalRedirect,
    NotServedQuery,
    ParserQuery,
    Position,
    PositionStub,
)

_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6"
_DETAIL_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4"
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


def remote_wire() -> dict[str, str]:
    # PRD #51 decision 41: use arbeitszeit=ho for homeoffice
    return {"arbeitszeit": "ho"}


def _contract_type(
    vertragsdauer: str | None,
) -> Literal["permanent", "fixed-term", "freelance"] | None:
    if vertragsdauer == "UNBEFRISTET":
        return "permanent"
    if vertragsdauer == "BEFRISTET":
        return "fixed-term"
    return None


def _employment_type(
    vollzeit: bool,
    teilzeit: bool,
) -> Literal["full-time", "part-time", "internship"] | None:
    if vollzeit:
        return "full-time"
    if teilzeit:
        return "part-time"
    return None


class BundesagenturParser:
    def __init__(
        self,
        *,
        run_log: RunLog,
        _http: ParserHttp | None = None,
    ) -> None:
        self._run_log = run_log
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

        seen: set[str] = set()
        count = 0
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
                if count >= query.max_results:
                    return
                ref: str = item.get("referenznummer") or ""
                if not ref or ref in seen:
                    continue
                seen.add(ref)
                title: str = item.get("stellenangebotsTitel") or ""
                if not title:
                    self._run_log.event(
                        "parser_bundesagentur_api",
                        "missing_title",
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
                count += 1

            page += 1

    def enrich(self, stub: PositionStub) -> Position | ExternalRedirect:
        raw_ref = stub.url.rsplit("/", 1)[-1]
        ref_b64 = base64.b64encode(raw_ref.encode()).decode()
        rest_url = f"{_DETAIL_BASE_URL}/jobdetails/{ref_b64}"
        try:
            raw = self._http.get(
                rest_url, error_prefix=f"Bundesagentur enrich failed for {stub.url}"
            )
        except HttpRedirectResponse as exc:
            location = exc.location
            source_host = urlparse(_DETAIL_BASE_URL).hostname or ""
            location_host = urlparse(location).hostname or "" if location else ""
            if location_host and location_host != source_host:
                return ExternalRedirect(stub, location)
            raise ParserError(
                f"Bundesagentur enrich 3xx redirect: location={location!r}"
            ) from exc
        data: dict[str, Any] = json.loads(raw)

        outbound: str | None = data.get("externeURL") or None
        raw_description = strip_html(data.get("stellenangebotsBeschreibung") or "")
        body = raw_description.strip()

        if outbound:
            skipped = body == ""
            self._run_log.event(
                "parser_bundesagentur_api",
                "external_redirect",
                stub_url=stub.url,
                outbound=outbound,
                skipped=skipped,
            )
            if skipped:
                return ExternalRedirect(stub, outbound)

        veroeffentlichung = data.get("veroeffentlichungszeitraum") or {}
        vollzeit = bool(data.get("arbeitszeitVollzeit"))
        teilzeit = any(
            bool(v) for k, v in data.items() if k.startswith("arbeitszeitTeilzeit")
        )

        return Position(
            stub=stub,
            raw_description=raw_description,
            contract_type=_contract_type(data.get("vertragsdauer")),
            employment_type=_employment_type(vollzeit, teilzeit),
            work_model=None,
            posted_date=parse_iso_date(veroeffentlichung.get("von")),
            deadline=parse_iso_date(data.get("bewerbungsschluss")),
        )


parser_class = BundesagenturParser


if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path

    from application_pipeline.parser_log import RunLog

    keyword = sys.argv[1] if len(sys.argv) > 1 else "Python"
    location = sys.argv[2] if len(sys.argv) > 2 else "Hamburg"
    query = ParserQuery(keyword=keyword, location=City(location), max_results=5)
    _run_log = RunLog(Path(tempfile.mkdtemp()))
    with BundesagenturParser(run_log=_run_log) as p:
        for stub in p.discover(query):
            print(stub)
