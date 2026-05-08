from __future__ import annotations

import html.parser
import re
from datetime import date


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


def strip_html(html_text: str) -> str:
    parser = _HtmlToText()
    parser.feed(html_text)
    return parser.result()


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
