from __future__ import annotations

import base64
import json
import sys
import urllib.parse
from collections.abc import Iterator
from typing import Any, Literal

import httpx

import application_pipeline.parser_log as parser_log
from application_pipeline.http import HttpRetryError

from ._text import parse_iso_date, strip_html
from .errors import ParserError
from .http import (
    HTTP_CONNECT_TIMEOUT,
    HTTP_READ_TIMEOUT,
    MAX_RETRIES,
    USER_AGENT,
    HttpGet,
    Throttle,
    check_response_status,
    request_with_retry,
)
from .location import NotServed, RemoteWire, Resolved, resolve
from .types import City, NotServedQuery, ParserQuery, Position, PositionStub

_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6"
_DETAIL_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4"
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


def _default_http_get(url: str, timeout: float) -> bytes:
    with httpx.Client(
        timeout=httpx.Timeout(HTTP_READ_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT),
        headers={"User-Agent": USER_AGENT, "X-API-Key": _API_KEY},
    ) as client:
        resp = client.get(url, timeout=timeout)
        check_response_status(resp, url)
        return resp.content


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
        _http_get: HttpGet | None = None,
        _timeout: float = HTTP_READ_TIMEOUT,
        _retries: int = MAX_RETRIES,
    ) -> None:
        self._http_get: HttpGet = _http_get or _default_http_get
        self._timeout = _timeout
        self._retries = _retries
        self._throttle = Throttle()

    def __enter__(self) -> "BundesagenturParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

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
            self._throttle.wait()
            params: dict[str, object] = {
                "was": query.keyword,
                "page": page,
                "size": _PAGE_SIZE,
                "angebotsart": 1,
                **extra_params,
            }
            url = f"{_BASE_URL}/jobs?{urllib.parse.urlencode(params)}"
            parser_log.record(
                "bundesagentur_api",
                "discover_page",
                q=query.keyword,
                page=page,
            )
            try:
                raw = request_with_retry(
                    url, self._timeout, self._retries, self._http_get
                )
                data: dict[str, Any] = json.loads(raw)
            except HttpRetryError as exc:
                raise ParserError(
                    f"Bundesagentur search failed: {exc}"
                ) from exc.__cause__

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
                    parser_log.record(
                        "bundesagentur_api",
                        "missing_title",
                        item=item,
                    )
                    continue
                lokationen: list[dict[str, Any]] = item.get("stellenlokationen") or []
                first_address = lokationen[0].get("adresse") or {} if lokationen else {}
                city: str | None = first_address.get("ort") or None
                ref_b64 = base64.b64encode(ref.encode()).decode()
                yield PositionStub(
                    url=f"{_DETAIL_BASE_URL}/jobdetails/{ref_b64}",
                    title=title,
                    source=_DISPLAY_NAME,
                    company=item.get("firma") or None,
                    location=city,
                    language="de",
                )
                count += 1

            page += 1

    def enrich(self, stub: PositionStub) -> Position:
        self._throttle.wait()
        try:
            raw = request_with_retry(
                stub.url, self._timeout, self._retries, self._http_get
            )
            data: dict[str, Any] = json.loads(raw)
        except HttpRetryError as exc:
            raise ParserError(
                f"Bundesagentur enrich failed for {stub.url}: {exc}"
            ) from exc.__cause__

        raw_description = strip_html(data.get("stellenangebotsBeschreibung") or "")
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

    keyword = sys.argv[1] if len(sys.argv) > 1 else "Python"
    location = sys.argv[2] if len(sys.argv) > 2 else "Hamburg"
    query = ParserQuery(keyword=keyword, location=City(location), max_results=5)
    with BundesagenturParser() as p:
        for stub in p.discover(query):
            print(stub)
