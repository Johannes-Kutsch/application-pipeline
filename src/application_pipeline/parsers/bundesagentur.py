from __future__ import annotations

import html.parser
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import date
from typing import Any, Literal

from application_pipeline.http import HttpRetryError

from .errors import ParserError
from .http import (
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    HttpGet,
    Throttle,
    request_with_retry,
)
from .types import Position, PositionStub

_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4"
_API_KEY = "jobboerse-jobsuche-ui"
_PAGE_SIZE = 25
_REMOTE_LOCATION = "bundesweit"


def _default_http_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"X-API-Key": _API_KEY})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()  # type: ignore[no-any-return]


class _HtmlToText(html.parser.HTMLParser):
    _BLOCK = frozenset(
        ["p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td"]
    )

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK:
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK:
            self._parts.append("\n\n")

    def result(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _strip_html(html_text: str) -> str:
    parser = _HtmlToText()
    parser.feed(html_text)
    return parser.result()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _contract_type(
    befristung: int | None,
) -> Literal["permanent", "fixed-term", "freelance"] | None:
    if befristung == 1:
        return "permanent"
    if befristung == 2:
        return "fixed-term"
    return None


def _employment_type(
    modelle: list[str] | None,
) -> Literal["full-time", "part-time", "internship"] | None:
    if not modelle:
        return None
    lower = {m.lower() for m in modelle}
    if "vz" in lower:
        return "full-time"
    if "tz" in lower:
        return "part-time"
    return None


class BundesagenturParser:
    def __init__(
        self,
        locations: list[str],
        include_remote: bool = False,
        *,
        _http_get: HttpGet | None = None,
        _timeout: float = DEFAULT_TIMEOUT,
        _retries: int = DEFAULT_RETRIES,
    ) -> None:
        self._locations = locations
        self._include_remote = include_remote
        self._http_get: HttpGet = _http_get or _default_http_get
        self._timeout = _timeout
        self._retries = _retries
        self._throttle = Throttle()

    def __enter__(self) -> "BundesagenturParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: str) -> Iterator[PositionStub]:
        seen: set[str] = set()
        search_locations = list(self._locations)
        if self._include_remote:
            search_locations.append(_REMOTE_LOCATION)
        for location in search_locations:
            yield from self._pages(query, location, seen)

    def _pages(
        self, query: str, location: str, seen: set[str]
    ) -> Iterator[PositionStub]:
        page = 0
        while True:
            self._throttle.wait()
            params = {
                "was": query,
                "wo": location,
                "page": page,
                "size": _PAGE_SIZE,
                "angebotsart": 1,
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

            items: list[dict[str, Any]] = data.get("stellenangebote") or []
            if not items:
                break

            for item in items:
                hash_id: str = item.get("hashId") or ""
                if not hash_id or hash_id in seen:
                    continue
                seen.add(hash_id)
                arbeitsort = item.get("arbeitsort") or {}
                yield PositionStub(
                    url=f"{_BASE_URL}/jobdetails/{hash_id}",
                    title=item["titel"],
                    source="bundesagentur",
                    company=item.get("arbeitgeber") or None,
                    location=arbeitsort.get("ort") or None,
                    language="de",
                )

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

        raw_description = _strip_html(data.get("stellenbeschreibung") or "")

        return Position(
            stub=stub,
            raw_description=raw_description,
            contract_type=_contract_type(data.get("befristung")),
            employment_type=_employment_type(data.get("arbeitszeitModelle")),
            work_model=None,
            posted_date=_parse_date(data.get("aktuelleVeroeffentlichungsdatum")),
            deadline=_parse_date(data.get("bewerbungsschluss")),
        )


parser_class = BundesagenturParser
