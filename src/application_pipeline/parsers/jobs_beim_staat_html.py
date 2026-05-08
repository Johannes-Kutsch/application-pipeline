from __future__ import annotations

import logging
import re
import urllib.request
from collections.abc import Iterator
from datetime import date, timedelta

from bs4 import BeautifulSoup

from application_pipeline.http import HttpRetryError
from application_pipeline.text import normalize

from .errors import ParserError
from .http import (
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    HttpGet,
    Throttle,
    request_with_retry,
)
from .types import Position, PositionStub

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
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()  # type: ignore[no-any-return]


class JobsBeimStaatParser:
    def __init__(
        self,
        locations: list[str],
        include_remote: bool = False,
        max_results: int = 1000,
        *,
        _http_get: HttpGet | None = None,
        _timeout: float = DEFAULT_TIMEOUT,
        _retries: int = DEFAULT_RETRIES,
    ) -> None:
        self._locations = locations
        self._include_remote = include_remote
        self._max_results = max_results
        self._http_get: HttpGet = _http_get or _default_http_get
        self._timeout = _timeout
        self._retries = _retries
        self._throttle = Throttle()

    def __enter__(self) -> "JobsBeimStaatParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: str) -> Iterator[PositionStub]:
        seen: set[str] = set()
        count = 0
        slugs: list[str] = []

        for loc in self._locations:
            key = normalize(loc)
            slug = _LOCATION_SLUGS.get(key) if key else None
            if slug is None:
                _log.warning(
                    "unknown_location parser_type=jobs_beim_staat_html location=%s", loc
                )
                continue
            slugs.append(slug)

        if self._include_remote:
            slugs.append("homeoffice")

        for slug in slugs:
            if count >= self._max_results:
                break
            for stub in self._fetch_listing_page(slug, seen):
                if count >= self._max_results:
                    break
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
            date_tag = item.select_one(".joblist__date")
            if date_tag:
                # Called for its INFO-on-unparseable side effect; PositionStub
                # has no posted_date field, so the value itself is unused here.
                _parse_posted_date(date_tag.get_text(strip=True), today)

            yield PositionStub(
                url=full_url,
                title=title,
                source=_DISPLAY_NAME,
                company=company or None,
                location=location or None,
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
                f"jobs-beim-staat enrich failed for {stub.url}: {exc}"
            ) from exc.__cause__

        soup = BeautifulSoup(raw, "html.parser")
        desc_tag = soup.select_one(".stellenangebot__description")
        raw_text = desc_tag.get_text(separator="\n") if desc_tag else ""
        raw_description = _normalize_description(raw_text)

        return Position(
            stub=stub,
            raw_description=raw_description,
        )


parser_class = JobsBeimStaatParser


if __name__ == "__main__":
    import sys

    query = sys.argv[1] if len(sys.argv) > 1 else "python"
    locations = sys.argv[2:] if len(sys.argv) > 2 else ["hamburg"]
    with JobsBeimStaatParser(locations=locations, max_results=10) as p:
        for stub in p.discover(query):
            print(stub.url, "|", stub.title, "|", stub.company, "|", stub.location)
            pos = p.enrich(stub)
            print("  description:", pos.raw_description[:80])
