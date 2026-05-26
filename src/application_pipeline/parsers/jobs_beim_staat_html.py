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
from pathlib import Path
from typing import Any, assert_never

from bs4 import BeautifulSoup, Tag

from application_pipeline.llm.body_strip import strip_to_text
from application_pipeline.parser_log import RunLog

from .http import ParserHttp
from .location import NotServed, RemoteWire, Resolved, resolve
from .types import (
    City,
    EnrichFailedError,
    EnrichResult,
    NotServedQuery,
    ParserQuery,
    PositionStub,
)

_log = logging.getLogger(__name__)

_BASE_URL = "https://www.jobs-beim-staat.de"
_REST_PATH = "/jobsearchRestApi"
_DISPLAY_NAME = "jobs-beim-staat"
_MAX_START = 10_000

serves_remote = True
has_native_enrich: bool = True


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
    if href.startswith("/stellenangebote/"):
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
        posted_date=posted_date,
        _warnings=warnings,
    )


class JobsBeimStaatParser:
    def __init__(
        self,
        *,
        run_log: RunLog,
        failures_dir: Path = Path("."),
        _http: ParserHttp | None = None,
    ) -> None:
        self._run_log = run_log
        self._http = _http if _http is not None else ParserHttp(run_log=run_log)

    def __enter__(self) -> "JobsBeimStaatParser":
        self._http.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._http.__exit__(*args)

    def enrich(self, stub: PositionStub) -> EnrichResult:
        outer_raw = self._http.get(
            stub.url,
            error_prefix=f"jobs-beim-staat outer page fetch failed for {stub.url}",
        )
        soup = BeautifulSoup(outer_raw, "html.parser")
        iframe = soup.find(id="myiframe")
        if not isinstance(iframe, Tag) or not iframe.get("src"):
            raise EnrichFailedError(f"no iframe element found on {stub.url}")
        iframe_src = str(iframe["src"])
        iframe_url = (
            f"{_BASE_URL}{iframe_src}" if iframe_src.startswith("/") else iframe_src
        )
        iframe_raw = self._http.enrich_get(
            iframe_url,
            error_prefix=f"jobs-beim-staat iframe fetch failed for {iframe_url}",
        )
        body = strip_to_text(iframe_raw.decode("utf-8"), None)
        return EnrichResult(stub=stub, body=body, mode="native")

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
        step: int | None = None
        start = 0
        today = date.today()

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

            self._run_log.event(
                "parser_jobs_beim_staat_html",
                "discover_page",
                q=q,
                place=place,
                start=start,
                step=step,
            )
            raw = self._http.get(
                url, error_prefix=f"jobs-beim-staat discover failed for {url}"
            )

            envelope: dict[str, Any] = json.loads(raw)
            jobs_html = str(envelope.get("jobs", ""))
            soup = BeautifulSoup(jobs_html, "html.parser")
            cards = soup.select(
                "div.serp-jobcontet-cards-container-joblist.jobcard[id]"
            )

            if not cards:
                break

            if step is None:
                step = len(cards)

            for card in cards:
                stub = _parse_card(card, today)
                if stub is not None:
                    yield stub

            start += step


parser_class = JobsBeimStaatParser

if __name__ == "__main__":
    import sys as _sys
    import tempfile
    from pathlib import Path

    from application_pipeline.parser_log import RunLog

    query = ParserQuery(
        keyword=_sys.argv[1] if len(_sys.argv) > 1 else "*",
        location=City("hamburg"),
    )
    _run_log = RunLog(Path(tempfile.mkdtemp()))
    with JobsBeimStaatParser(run_log=_run_log) as p:
        items = list(p.discover(query))
    stubs = [s for s in items if isinstance(s, PositionStub)]
    print(f"discover: {len(stubs)} stubs")
