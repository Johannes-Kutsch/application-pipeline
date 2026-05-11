from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Iterator
from typing import Any, Literal

import httpx

from application_pipeline.http import HttpRetryError
from application_pipeline.text import normalize

from ._http import HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT, USER_AGENT
from ._text import parse_iso_date, strip_html
from .errors import ParserError
from .http import (
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    HttpGet,
    Throttle,
    check_response_status,
    request_with_retry,
)
from .types import City, ParserQuery, Position, PositionStub

_log = logging.getLogger(__name__)

_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6"
_API_KEY = "jobboerse-jobsuche"
_PAGE_SIZE = 25
_DISPLAY_NAME = "Bundesagentur"

# Keys are normalize()-ed location strings; values are the API's wo= slug.
_LOCATION_SLUGS: dict[str, str] = {
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
        _timeout: float = DEFAULT_TIMEOUT,
        _retries: int = DEFAULT_RETRIES,
    ) -> None:
        self._http_get: HttpGet = _http_get or _default_http_get
        self._timeout = _timeout
        self._retries = _retries
        self._throttle = Throttle()

    def __enter__(self) -> "BundesagenturParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> Iterator[PositionStub]:
        extra_params: dict[str, object] = {}
        if query.location is None:
            # Remote search: PRD #51 decision 41 — use arbeitszeit=ho for homeoffice
            extra_params["arbeitszeit"] = "ho"
        else:
            key = normalize(query.location)
            slug = _LOCATION_SLUGS.get(key) if key else None
            if slug is None:
                _log.warning(
                    "unmapped_location parser_type=bundesagentur_api location=%s",
                    query.location,
                )
                return
            extra_params["wo"] = slug

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
                lokationen: list[dict[str, Any]] = item.get("stellenlokationen") or []
                first_address = lokationen[0].get("adresse") or {} if lokationen else {}
                city: str | None = first_address.get("ort") or None
                yield PositionStub(
                    url=f"{_BASE_URL}/jobdetails/{ref}",
                    title=item["stellenangebotsTitel"],
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

        raw_description = strip_html(data.get("stellenbeschreibung") or "")
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
