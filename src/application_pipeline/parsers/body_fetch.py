"""Generic body fetch and strip helper for the fallback enrich path."""

from __future__ import annotations

from pathlib import Path

import httpx

from application_pipeline.llm.body_strip import strip_to_text
from application_pipeline.parsers.types import EnrichFailedError

_TOKEN_CAP_DEFAULT = 8_000
_CHARS_PER_TOKEN = 4


class OversizedBodyError(Exception):
    """Raised by fetch_and_strip when the stripped body exceeds the token cap.

    Raw HTML is already stashed to failures_dir/oversized/ before this is raised.
    Callers that want to log the event can read url, source, and body_len.
    """

    def __init__(self, url: str, source: str, body_len: int) -> None:
        super().__init__(f"oversized body for {url}: {body_len} chars")
        self.url = url
        self.source = source
        self.body_len = body_len


def fetch_and_strip(
    url: str,
    *,
    body_selector: str | None,
    source: str,
    failures_dir: Path,
    token_cap: int = _TOKEN_CAP_DEFAULT,
) -> str:
    """Fetch *url* and strip HTML to plaintext.

    Redirect-following is enabled. Raises EnrichFailedError on unrecoverable
    HTTP failure. Raises OversizedBodyError when the stripped body exceeds the
    token cap (raw HTML is stashed to failures_dir/oversized/ before raising).
    """
    try:
        with httpx.Client(follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError as exc:
        raise EnrichFailedError(f"{url}: {exc}") from exc

    text = strip_to_text(html, body_selector)

    if len(text) > token_cap * _CHARS_PER_TOKEN:
        _stash(failures_dir / "oversized", source, url, html)
        raise OversizedBodyError(url, source, len(text))

    return text


def _stash(
    stash_dir: Path, source: str, url: str, content: str, *, ext: str = "html"
) -> None:
    stash_dir.mkdir(parents=True, exist_ok=True)
    slug = url.replace("https://", "").replace("http://", "").replace("/", "-")
    path = stash_dir / f"{source}-{slug}.{ext}"
    path.write_text(content, encoding="utf-8")
