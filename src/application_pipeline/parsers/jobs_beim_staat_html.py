from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import date, timedelta

import httpx
from bs4 import BeautifulSoup

from application_pipeline.http import HttpRetryError
from application_pipeline.text import normalize

from ._http import HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT, USER_AGENT
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

_log = logging.getLogger(__name__)

_BASE_URL = "https://www.jobs-beim-staat.de"
_DISPLAY_NAME = "jobs-beim-staat"

# Keys are normalize()-ed city names; values are URL path slugs.
_LOCATION_SLUGS: dict[str, str] = {
    "bonn": "bonn",
    "berlin": "berlin",
    "bremen": "bremen",
    "dortmund": "dortmund",
    "dresden": "dresden",
    "duesseldorf": "duesseldorf",
    "düsseldorf": "duesseldorf",
    "erfurt": "erfurt",
    "essen": "essen",
    "frankfurt": "frankfurt-am-main",
    "frankfurt am main": "frankfurt-am-main",
    "hamburg": "hamburg",
    "hannover": "hannover",
    "homeoffice": "homeoffice",
    "kiel": "kiel",
    "köln": "koeln",
    "koeln": "koeln",
    "leipzig": "leipzig",
    "magdeburg": "magdeburg",
    "mainz": "mainz",
    "münchen": "muenchen",
    "muenchen": "muenchen",
    "nürnberg": "nuernberg",
    "nuernberg": "nuernberg",
    "potsdam": "potsdam",
    "saarbrücken": "saarbruecken",
    "saarbruecken": "saarbruecken",
    "schwerin": "schwerin",
    "stuttgart": "stuttgart",
    "wiesbaden": "wiesbaden",
}

_HEUTE_RE = re.compile(r"^heute$", re.IGNORECASE)
_GESTERN_RE = re.compile(r"^gestern$", re.IGNORECASE)
_TAGEN_RE = re.compile(r"^vor\s+(\d+)\s+Tag(?:en)?$", re.IGNORECASE)
_WOCHEN_RE = re.compile(r"^vor\s+(\d+)\s+Woche(?:n)?$", re.IGNORECASE)
_DMY_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def _parse_posted_date(raw: str, today: date) -> date | None:
    s = raw.strip()
    if _HEUTE_RE.match(s):
        return today
    if _GESTERN_RE.match(s):
        return today - timedelta(days=1)
    m = _TAGEN_RE.match(s)
    if m:
        return today - timedelta(days=int(m.group(1)))
    m = _WOCHEN_RE.match(s)
    if m:
        return today - timedelta(weeks=int(m.group(1)))
    m = _DMY_RE.match(s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    _log.info("unparseable_date parser_type=jobs_beim_staat_html raw=%s", raw)
    return None


def _normalize_description(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.split("\n")]
    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return collapsed.strip()


def _default_http_get(url: str, timeout: float) -> bytes:
    with httpx.Client(
        timeout=httpx.Timeout(HTTP_READ_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT),
        headers={"User-Agent": USER_AGENT},
    ) as client:
        resp = client.get(url, timeout=timeout)
        check_response_status(resp, url)
        return resp.content


class JobsBeimStaatParser:
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

    def __enter__(self) -> "JobsBeimStaatParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> Iterator[PositionStub]:
        if query.location is None:
            return
        key = normalize(query.location)
        slug = _LOCATION_SLUGS.get(key) if key else None
        if slug is None:
            _log.warning(
                "unknown_location parser_type=jobs_beim_staat_html location=%s",
                query.location,
            )
            return
        seen: set[str] = set()
        count = 0
        for stub in self._fetch_listing_page(slug, seen):
            if count >= query.max_results:
                return
            yield stub
            count += 1

    def _fetch_listing_page(self, slug: str, seen: set[str]) -> Iterator[PositionStub]:
        url = f"{_BASE_URL}/jobs/{slug}"
        self._throttle.wait()
        try:
            raw = request_with_retry(url, self._timeout, self._retries, self._http_get)
        except HttpRetryError as exc:
            raise ParserError(
                f"jobs-beim-staat discover failed for {url}: {exc}"
            ) from exc.__cause__

        soup = BeautifulSoup(raw, "html.parser")
        today = date.today()

        for item in soup.select("article.joblist__item"):
            link = item.select_one("a")
            if not link:
                continue
            href: str = link.get("href") or ""  # type: ignore[assignment]
            if not href:
                continue
            full_url = f"{_BASE_URL}{href}" if href.startswith("/") else href
            if full_url in seen:
                continue
            seen.add(full_url)

            title = link.get_text(strip=True)
            employer_tag = item.select_one(".joblist__employer")
            company = employer_tag.get_text(strip=True) if employer_tag else None
            location_tag = item.select_one(".joblist__location")
            location = location_tag.get_text(strip=True) if location_tag else None
            posted_date: date | None = None
            date_tag = item.select_one(".joblist__date")
            if date_tag:
                posted_date = _parse_posted_date(date_tag.get_text(strip=True), today)

            yield PositionStub(
                url=full_url,
                title=title,
                source=_DISPLAY_NAME,
                company=company or None,
                location=location or None,
                language="de",
                posted_date=posted_date,
            )

    def enrich(self, stub: PositionStub) -> Position:
        self._throttle.wait()
        try:
            raw = request_with_retry(
                stub.url, self._timeout, self._retries, self._http_get
            )
        except HttpRetryError as exc:
            raise ParserError(
                f"jobs-beim-staat enrich failed for {stub.url}: {exc}"
            ) from exc.__cause__

        soup = BeautifulSoup(raw, "html.parser")
        desc_tag = soup.select_one(".stellenangebot__description")
        raw_text = desc_tag.get_text(separator="\n") if desc_tag else ""
        raw_description = _normalize_description(raw_text)

        return Position(
            stub=stub,
            raw_description=raw_description,
            posted_date=stub.posted_date,
        )


parser_class = JobsBeimStaatParser
