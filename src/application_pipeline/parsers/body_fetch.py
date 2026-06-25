"""Generic body fetch and strip helper for the fallback enrich path."""

from __future__ import annotations

from pathlib import Path

from application_pipeline.parsers.body_text import html_to_raw_description
from application_pipeline.parsers.http import ParserHttp

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
    http: ParserHttp,
    token_cap: int = _TOKEN_CAP_DEFAULT,
) -> str:
    """Fetch *url* via *http* and strip HTML to plaintext.

    Delegates HTTP fetch (including retry-with-backoff, pacing, and error
    classification) to *http.enrich_get()*. Raises EnrichFailedError on
    non-retryable HTTP failures (404, 400, 422). Raises OversizedBodyError
    when the stripped body exceeds the token cap (raw HTML is stashed to
    failures_dir/oversized/ before raising).
    """
    content = http.enrich_get(url, error_prefix=url)
    html = content.decode("utf-8", errors="replace")

    text = html_to_raw_description(html, body_selector)

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
