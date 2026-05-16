from __future__ import annotations

import html.parser
import json
import sys
import urllib.parse
from collections.abc import Iterator
from typing import Any, Literal, NoReturn

import httpx

import application_pipeline.parser_log as parser_log
from application_pipeline.http import HttpRetryError
from application_pipeline.text import normalize

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

_PARSER_TYPE = "stellen_hamburg_api"
_DISPLAY_NAME = "stellen.hamburg"
_SEARCH_URL = "https://api-stellen.hamburg.de/search/"
_PAGE_SIZE = 25


def serves(name: str) -> bool:
    return normalize(name) == "hamburg"


def to_wire(name: str) -> str:
    return "Hamburg"


serves_remote: bool = False


def remote_wire() -> NoReturn:
    raise AssertionError("stellen_hamburg_api does not serve remote")


def _default_http_get(url: str, timeout: float) -> bytes:
    with httpx.Client(
        timeout=httpx.Timeout(HTTP_READ_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT),
        headers={"User-Agent": USER_AGENT},
    ) as client:
        resp = client.get(url, timeout=timeout)
        check_response_status(resp, url)
        return resp.content


class _JsonLdExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_jsonld = False
        self._chunks: list[str] = []
        self._job_posting: dict[str, Any] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            attrs_dict = dict(attrs)
            if attrs_dict.get("type") == "application/ld+json":
                self._in_jsonld = True
                self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            try:
                data = json.loads("".join(self._chunks))
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("@type") == "JobPosting":
                        self._job_posting = entry
                        break
            except (json.JSONDecodeError, ValueError):
                pass

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._chunks.append(data)

    def result(self) -> dict[str, Any]:
        return self._job_posting


def _extract_jsonld(html_bytes: bytes) -> dict[str, Any]:
    extractor = _JsonLdExtractor()
    extractor.feed(html_bytes.decode("utf-8", errors="replace"))
    return extractor.result()


def _employment_type(
    value: str | None,
) -> Literal["full-time", "part-time", "internship"] | None:
    if not value:
        return None
    v = value.upper()
    if v == "FULL_TIME":
        return "full-time"
    if v == "PART_TIME":
        return "part-time"
    return None


def _search_url(keyword: str, page_number: int) -> str:
    data = json.dumps(
        {
            "SearchCriteria": [
                {
                    "CriterionName": "PositionFormattedDescription.Content",
                    "CriterionValue": keyword,
                }
            ],
            "PageSize": _PAGE_SIZE,
            "PageNumber": page_number,
        },
        separators=(",", ":"),
    )
    return f"{_SEARCH_URL}?{urllib.parse.urlencode({'data': data})}"


class StellenHamburgParser:
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

    def __enter__(self) -> "StellenHamburgParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> Iterator[PositionStub | NotServedQuery]:
        match resolve(query.location, sys.modules[__name__]):
            case NotServed():
                yield NotServedQuery()
                return
            case RemoteWire(_):
                return
            case Resolved(_):
                pass

        seen: set[str] = set()
        count = 0
        page_number = 1
        while True:
            self._throttle.wait()
            url = _search_url(query.keyword, page_number)
            parser_log.record(
                _PARSER_TYPE,
                "discover_page",
                q=query.keyword,
                start=(page_number - 1) * _PAGE_SIZE,
            )
            try:
                raw = request_with_retry(
                    url, self._timeout, self._retries, self._http_get
                )
                data: dict[str, Any] = json.loads(raw)
            except HttpRetryError as exc:
                raise ParserError(
                    f"StellenHamburg search failed: {exc}"
                ) from exc.__cause__

            result: dict[str, Any] = data.get("SearchResult") or {}
            items: list[dict[str, Any]] = result.get("SearchResultItems") or []
            if not items:
                break

            any_new = False
            for item in items:
                if count >= query.max_results:
                    return
                obj_id: str = item.get("MatchedObjectId") or ""
                if not obj_id or obj_id in seen:
                    continue
                seen.add(obj_id)
                any_new = True
                descriptor: dict[str, Any] = item.get("MatchedObjectDescriptor") or {}
                yield self._to_stub(descriptor, obj_id)
                count += 1

            total: int = result.get("SearchResultCountAll") or 0
            items_seen = (page_number - 1) * _PAGE_SIZE + len(items)
            page_number += 1
            if not any_new or items_seen >= total:
                break

    def _to_stub(self, descriptor: dict[str, Any], obj_id: str) -> PositionStub:
        positions: list[dict[str, Any]] = descriptor.get("PositionLocation") or []
        location: str | None = None
        if positions:
            location = positions[0].get("CountrySubDivisionName") or None

        url: str = descriptor.get("PositionURI") or (
            f"https://stellen.hamburg.de/index.php?ac=jobad&id={obj_id}"
        )

        return PositionStub(
            url=url,
            title=descriptor.get("PositionTitle") or obj_id,
            source=_DISPLAY_NAME,
            company=descriptor.get("OrganizationName") or None,
            location=location,
        )

    def enrich(self, stub: PositionStub) -> Position:
        self._throttle.wait()
        try:
            raw = request_with_retry(
                stub.url, self._timeout, self._retries, self._http_get
            )
        except HttpRetryError as exc:
            raise ParserError(
                f"StellenHamburg enrich failed for {stub.url}: {exc}"
            ) from exc.__cause__

        job_data = _extract_jsonld(raw)
        raw_description = strip_html(job_data.get("description") or "")

        return Position(
            stub=stub,
            raw_description=raw_description,
            contract_type=None,
            employment_type=_employment_type(job_data.get("employmentType")),
            work_model=None,
            posted_date=parse_iso_date(job_data.get("datePosted")),
            deadline=parse_iso_date(job_data.get("validThrough")),
            salary=None,
        )


parser_class = StellenHamburgParser

if __name__ == "__main__":
    import sys

    query = ParserQuery(
        keyword=sys.argv[1] if len(sys.argv) > 1 else "*",
        location=City("hamburg"),
        max_results=5,
    )
    with StellenHamburgParser() as p:
        items = list(p.discover(query))
    stubs = [s for s in items if isinstance(s, PositionStub)]
    print(f"discover: {len(stubs)} stubs")
    if stubs:
        with StellenHamburgParser() as p:
            pos = p.enrich(stubs[0])
        print(f"enrich: {len(pos.raw_description)} chars")
