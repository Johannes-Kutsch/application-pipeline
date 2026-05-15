"""jobs-beim-staat HTML parser.

Fetches job cards from the jobsearchRestApi JSON-envelope endpoint at
jobs-beim-staat.de.  The ``jobs`` field in each envelope is an HTML
fragment; cards are parsed from it with BeautifulSoup.

``q="*"`` maps to an empty ``q=`` value — the REST endpoint returns all
jobs matching place/radius when the keyword field is empty (parser-local
convention).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.parse
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any, assert_never

import httpx
from bs4 import BeautifulSoup, Tag

import application_pipeline.parser_log as parser_log
from application_pipeline.http import HttpRetryError

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
from .types import (
    City,
    ExternalRedirect,
    NotServedQuery,
    ParserQuery,
    Position,
    PositionStub,
)

_log = logging.getLogger(__name__)

_BASE_URL = "https://www.jobs-beim-staat.de"
_REST_PATH = "/jobsearchRestApi"
_DISPLAY_NAME = "jobs-beim-staat"
_MAX_START = 10_000

serves_remote = True


def serves(name: str) -> bool:
    return True


def to_wire(name: str) -> str:
    return name


def remote_wire() -> str:
    return "homeoffice"


_HEUTE_RE = re.compile(r"^heute$", re.IGNORECASE)
_GESTERN_RE = re.compile(r"^gestern$", re.IGNORECASE)
_TAGEN_RE = re.compile(r"^vor\s+(\d+)\s+Tag(?:en)?$", re.IGNORECASE)
_WOCHEN_RE = re.compile(r"^vor\s+(\d+)\s+Woche(?:n)?$", re.IGNORECASE)
_DMY_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def _parse_posted_date(raw: str, today: date) -> tuple[date | None, str | None]:
    s = raw.strip()
    if _HEUTE_RE.match(s):
        return today, None
    if _GESTERN_RE.match(s):
        return today - timedelta(days=1), None
    m = _TAGEN_RE.match(s)
    if m:
        return today - timedelta(days=int(m.group(1))), None
    m = _WOCHEN_RE.match(s)
    if m:
        return today - timedelta(weeks=int(m.group(1))), None
    m = _DMY_RE.match(s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))), None
        except ValueError:
            pass
    return None, f"unparseable_date raw={raw}"


def _id_from_query(url: str) -> str | None:
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return params["id"][0] if "id" in params else None


def _extract_job_id(soup: BeautifulSoup) -> str | None:
    raw_url_input = soup.find("input", {"name": "raw-url"})
    if isinstance(raw_url_input, Tag):
        job_id = _id_from_query(str(raw_url_input.get("value", "")))
        if job_id is not None:
            return job_id
    iframe = soup.find("iframe")
    if isinstance(iframe, Tag):
        return _id_from_query(str(iframe.get("src", "")))
    return None


def _find_outbound_href(soup: BeautifulSoup) -> str | None:
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        host = urllib.parse.urlparse(href).netloc
        if host and "jobs-beim-staat.de" not in host:
            return href
    return None


def _normalize_description(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.split("\n")]
    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return collapsed.strip()


def _text_after_icon(card: Tag, icon_suffix: str) -> str | None:
    for span in card.select(".serp-jobcontet-cards-container-text"):
        img = span.find("img")
        if not isinstance(img, Tag):
            continue
        if str(img.get("src", "")).endswith(icon_suffix):
            for el in span.find_all("img"):
                el.extract()
            return span.get_text(strip=True) or None
    return None


def _parse_card(card: Tag, today: date) -> PositionStub | None:
    link = card.select_one("h3 > a")
    if not isinstance(link, Tag):
        return None
    href = str(link.get("href") or "")
    if not href:
        return None
    full_url = f"{_BASE_URL}{href}" if href.startswith("/") else href
    title = link.get_text(strip=True)

    company: str | None = None
    company_el = card.select_one("[data-company]")
    if isinstance(company_el, Tag):
        raw = str(company_el.get("data-company") or "").strip()
        company = raw or None
    if company is None:
        fw500 = card.select_one(".serp-jobcontet-cards-container-text.fw-500")
        if fw500:
            company = fw500.get_text(strip=True) or None

    location = _text_after_icon(card, "location-pin.png")
    raw_date = _text_after_icon(card, "history.png")
    posted_date: date | None = None
    warnings: tuple[str, ...] = ()
    if raw_date:
        posted_date, warning = _parse_posted_date(raw_date, today)
        if warning is not None:
            warnings = (warning,)

    return PositionStub(
        url=full_url,
        title=title,
        source=_DISPLAY_NAME,
        company=company,
        location=location,
        language="de",
        posted_date=posted_date,
        _warnings=warnings,
    )


class JobsBeimStaatParser:
    def __init__(
        self,
        *,
        _http_get: HttpGet | None = None,
        _timeout: float = HTTP_READ_TIMEOUT,
        _retries: int = MAX_RETRIES,
    ) -> None:
        self._timeout = _timeout
        self._retries = _retries
        self._throttle = Throttle()
        if _http_get is None:
            self._client: httpx.Client | None = httpx.Client(
                timeout=httpx.Timeout(HTTP_READ_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT),
                headers={"User-Agent": USER_AGENT},
            )
            _client = self._client

            def _default_get(url: str, timeout: float) -> bytes:
                resp = _client.get(url, timeout=timeout)
                check_response_status(resp, url)
                return resp.content

            self._http_get: HttpGet = _default_get
        else:
            self._client = None
            self._http_get = _http_get

    def __enter__(self) -> "JobsBeimStaatParser":
        return self

    def __exit__(self, *args: object) -> None:
        if self._client is not None:
            self._client.close()

    def discover(self, query: ParserQuery) -> Iterator[PositionStub | NotServedQuery]:
        place: str
        match resolve(query.location, sys.modules[__name__]):
            case Resolved(wire):
                place = wire
            case RemoteWire(payload):
                place = str(payload)
            case NotServed():
                yield NotServedQuery()
                return
            case _ as unreachable:
                assert_never(unreachable)

        q = "" if query.keyword == "*" else query.keyword
        count = 0
        step: int | None = None
        start = 0
        today = date.today()
        seen_urls: set[str] = set()

        while True:
            if start >= _MAX_START:
                break

            params: dict[str, str] = {
                "q": q,
                "place": place,
                "radius": "20",
                "sort": "date",
                "start": str(start),
                "viewType": "card",
            }
            url = f"{_BASE_URL}{_REST_PATH}?{urllib.parse.urlencode(params)}"

            parser_log.record(
                "jobs_beim_staat_html",
                "discover_page",
                q=q,
                place=place,
                start=start,
                step=step,
            )
            self._throttle.wait()
            try:
                raw = request_with_retry(
                    url, self._timeout, self._retries, self._http_get
                )
            except HttpRetryError as exc:
                raise ParserError(
                    f"jobs-beim-staat discover failed for {url}: {exc}"
                ) from exc.__cause__

            envelope: dict[str, Any] = json.loads(raw)
            jobs_html = str(envelope.get("jobs", ""))
            soup = BeautifulSoup(jobs_html, "html.parser")
            cards = soup.select("div.serp-jobcontet-cards-container-joblist.jobcard")

            if not cards:
                break

            if step is None:
                step = len(cards)

            page_stubs = [
                stub for card in cards if (stub := _parse_card(card, today)) is not None
            ]
            page_urls = {stub.url for stub in page_stubs}

            if page_urls and page_urls.issubset(seen_urls):
                break

            for stub in page_stubs:
                if count >= query.max_results:
                    return
                if stub.url not in seen_urls:
                    seen_urls.add(stub.url)
                    yield stub
                    count += 1

            start += step

    def enrich(self, stub: PositionStub) -> Position | ExternalRedirect:
        self._throttle.wait()
        try:
            wrapper_raw = request_with_retry(
                stub.url, self._timeout, self._retries, self._http_get
            )
        except HttpRetryError as exc:
            raise ParserError(
                f"jobs-beim-staat enrich failed for {stub.url}: {exc}"
            ) from exc.__cause__

        wrapper_soup = BeautifulSoup(wrapper_raw, "html.parser")
        job_id = _extract_job_id(wrapper_soup)
        if job_id is None:
            outbound = _find_outbound_href(wrapper_soup)
            if outbound is not None:
                return ExternalRedirect(stub, outbound)
            raise ParserError(
                f"jobs-beim-staat enrich: no iframe target found in wrapper {stub.url}"
            )

        iframe_url = f"{_BASE_URL}/stellenanzeigen-details/?id={job_id}"
        self._throttle.wait()
        try:
            iframe_raw = request_with_retry(
                iframe_url, self._timeout, self._retries, self._http_get
            )
        except HttpRetryError as exc:
            raise ParserError(
                f"jobs-beim-staat enrich failed for {iframe_url}: {exc}"
            ) from exc.__cause__

        iframe_soup = BeautifulSoup(iframe_raw, "html.parser")
        raw_description = _normalize_description(iframe_soup.get_text(separator="\n"))

        return Position(
            stub=stub,
            raw_description=raw_description,
            posted_date=stub.posted_date,
            _warnings=stub._warnings,
        )


parser_class = JobsBeimStaatParser

if __name__ == "__main__":
    import sys as _sys

    query = ParserQuery(
        keyword=_sys.argv[1] if len(_sys.argv) > 1 else "*",
        location=City("hamburg"),
        max_results=5,
    )
    with JobsBeimStaatParser() as p:
        items = list(p.discover(query))
    stubs = [s for s in items if isinstance(s, PositionStub)]
    print(f"discover: {len(stubs)} stubs")
    if stubs:
        with JobsBeimStaatParser() as p:
            result = p.enrich(stubs[0])
        if isinstance(result, Position):
            print(f"enrich: {len(result.raw_description)} chars")
        else:
            print(f"enrich: external_redirect outbound={result.outbound_url}")
