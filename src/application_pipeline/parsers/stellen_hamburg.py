from __future__ import annotations

import html.parser
import json
import re
import urllib.request
from collections.abc import Iterator
from datetime import date
from typing import Any, Callable, Literal

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

_SEARCH_URL = "https://api-stellen.hamburg.de/search/"
_PAGE_SIZE = 25

HttpPost = Callable[[str, bytes, float], bytes]


def _default_http_post(url: str, body: bytes, timeout: float) -> bytes:
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()  # type: ignore[no-any-return]


def _default_http_get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()  # type: ignore[no-any-return]


def _post_with_retry(
    url: str,
    body: bytes,
    timeout: float,
    retries: int,
    http_post: HttpPost,
) -> bytes:
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            return http_post(url, body, timeout)
        except Exception as exc:
            last_exc = exc
    raise HttpRetryError(
        f"HTTP POST failed after {retries} retries: {last_exc}"
    ) from last_exc


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
                if isinstance(data, dict) and data.get("@type") == "JobPosting":
                    self._job_posting = data
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


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


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


class StellenHamburgParser:
    def __init__(
        self,
        locations: list[str],
        include_remote: bool = False,
        *,
        _http_get: HttpGet | None = None,
        _http_post: HttpPost | None = None,
        _timeout: float = DEFAULT_TIMEOUT,
        _retries: int = DEFAULT_RETRIES,
    ) -> None:
        self._locations = locations
        self._include_remote = include_remote
        self._http_get: HttpGet = _http_get or _default_http_get
        self._http_post: HttpPost = _http_post or _default_http_post
        self._timeout = _timeout
        self._retries = _retries
        self._throttle = Throttle()

    def __enter__(self) -> "StellenHamburgParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: str) -> Iterator[PositionStub]:
        seen: set[str] = set()
        offset = 0
        while True:
            self._throttle.wait()
            body = json.dumps(
                {
                    "SearchParameters": {
                        "PositionTitle": query,
                        "NumberOfResults": _PAGE_SIZE,
                        "Offset": offset,
                    }
                }
            ).encode()
            try:
                raw = _post_with_retry(
                    _SEARCH_URL, body, self._timeout, self._retries, self._http_post
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
                obj_id: str = item.get("MatchedObjectId") or ""
                if not obj_id or obj_id in seen:
                    continue
                seen.add(obj_id)
                any_new = True
                descriptor: dict[str, Any] = item.get("MatchedObjectDescriptor") or {}
                yield self._to_stub(descriptor, obj_id)

            total: int = result.get("SearchResultCountAll") or 0
            offset += len(items)
            if not any_new or offset >= total:
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
            source="stellen_hamburg",
            company=descriptor.get("OrganizationName") or None,
            location=location,
            language="de",
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
        raw_description = _strip_html(job_data.get("description") or "")

        return Position(
            stub=stub,
            raw_description=raw_description,
            contract_type=None,
            employment_type=_employment_type(job_data.get("employmentType")),
            work_model=None,
            posted_date=_parse_date(job_data.get("datePosted")),
            deadline=_parse_date(job_data.get("validThrough")),
            salary=None,
        )


parser_class = StellenHamburgParser
