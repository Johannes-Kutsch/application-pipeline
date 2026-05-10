from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterator
from typing import Any, Literal

import httpx

from application_pipeline.http import HttpRetryError

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
from .types import ParserQuery, Position, PositionStub

_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4"
_API_KEY = "jobboerse-jobsuche-ui"
_PAGE_SIZE = 25


def _default_http_get(url: str, timeout: float) -> bytes:
    with httpx.Client(
        timeout=httpx.Timeout(HTTP_READ_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT),
        headers={"User-Agent": USER_AGENT, "X-API-Key": _API_KEY},
    ) as client:
        resp = client.get(url, timeout=timeout)
        check_response_status(resp, url)
        return resp.content


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
        seen: set[str] = set()
        count = 0
        page = 0
        while True:
            self._throttle.wait()
            params: dict[str, object] = {
                "was": query.keyword,
                "page": page,
                "size": _PAGE_SIZE,
                "angebotsart": 1,
            }
            if query.location is not None:
                params["wo"] = query.location
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
                if count >= query.max_results:
                    return
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

        return Position(
            stub=stub,
            raw_description=raw_description,
            contract_type=_contract_type(data.get("befristung")),
            employment_type=_employment_type(data.get("arbeitszeitModelle")),
            work_model=None,
            posted_date=parse_iso_date(data.get("aktuelleVeroeffentlichungsdatum")),
            deadline=parse_iso_date(data.get("bewerbungsschluss")),
        )


parser_class = BundesagenturParser
