from __future__ import annotations

import json
import sys
import urllib.parse
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

from application_pipeline.parser_log import RunLog
from application_pipeline.text import normalize

from .body_fetch import fetch_and_strip
from .http import ParserHttp
from .location import NotServed, RemoteWire, Resolved, resolve
from .types import (
    City,
    EnrichResult,
    NotServedQuery,
    ParserQuery,
    PositionStub,
)

_PARSER_TYPE = "stellen_hamburg_api"
_DISPLAY_NAME = "stellen.hamburg"
_SEARCH_URL = "https://api-stellen.hamburg.de/search/"
_PAGE_SIZE = 25


def serves(name: str) -> bool:
    return normalize(name) == "hamburg"


def to_wire(name: str) -> str:
    return "Hamburg"


serves_remote: bool = False
has_native_enrich: bool = False


def remote_wire() -> NoReturn:
    raise AssertionError("stellen_hamburg_api does not serve remote")


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
    _body_selector: str | None = None

    def __init__(
        self,
        *,
        run_log: RunLog,
        failures_dir: Path = Path("."),
        _http: ParserHttp | None = None,
    ) -> None:
        self._run_log = run_log
        self._failures_dir = failures_dir
        self._http = _http if _http is not None else ParserHttp(run_log=run_log)

    def __enter__(self) -> "StellenHamburgParser":
        self._http.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._http.__exit__(*args)

    def enrich(self, stub: PositionStub) -> EnrichResult:
        body = fetch_and_strip(
            stub.url,
            body_selector=self._body_selector,
            source=stub.source,
            failures_dir=self._failures_dir,
            http=self._http,
        )
        return EnrichResult(stub=stub, body=body, mode="fallback")

    def discover(self, query: ParserQuery) -> Iterator[PositionStub | NotServedQuery]:
        match resolve(query.location, sys.modules[__name__]):
            case NotServed():
                yield NotServedQuery()
                return
            case RemoteWire(_):
                return
            case Resolved(_):
                pass

        page_number = 1
        while True:
            url = _search_url(query.keyword, page_number)
            self._run_log.event(
                "parser_" + _PARSER_TYPE,
                "discover_page",
                q=query.keyword,
                start=(page_number - 1) * _PAGE_SIZE,
            )
            raw = self._http.get(url, error_prefix="StellenHamburg search failed")
            data: dict[str, Any] = json.loads(raw)

            result: dict[str, Any] = data.get("SearchResult") or {}
            items: list[dict[str, Any]] = result.get("SearchResultItems") or []
            if not items:
                break

            for item in items:
                obj_id: str = item.get("MatchedObjectId") or ""
                if not obj_id:
                    continue
                descriptor: dict[str, Any] = item.get("MatchedObjectDescriptor") or {}
                yield self._to_stub(descriptor, obj_id)

            total: int = result.get("SearchResultCountAll") or 0
            items_seen = (page_number - 1) * _PAGE_SIZE + len(items)
            page_number += 1
            if items_seen >= total:
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
            posted_date=_parse_date(descriptor.get("PublicationStartDate")),
            deadline=_parse_date(descriptor.get("PublicationEndDate")),
        )


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


parser_class = StellenHamburgParser

if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path

    from application_pipeline.parser_log import RunLog

    query = ParserQuery(
        keyword=sys.argv[1] if len(sys.argv) > 1 else "*",
        location=City("hamburg"),
    )
    _run_log = RunLog(Path(tempfile.mkdtemp()))
    with StellenHamburgParser(run_log=_run_log) as p:
        items = list(p.discover(query))
    stubs = [s for s in items if isinstance(s, PositionStub)]
    print(f"discover: {len(stubs)} stubs")
