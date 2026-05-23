"""Body strip helper — extract plaintext from HTML via CSS selector or library fallback."""

from __future__ import annotations

from bs4 import BeautifulSoup


def strip_to_text(html: str, selector: str | None) -> str:
    """Return stripped plaintext from *html*.

    When *selector* is supplied, extract that CSS node and return its text.
    When *selector* is None, fall back to trafilatura for generic extraction.
    Returns an empty string when no usable content is found.
    """
    if selector is not None:
        soup = BeautifulSoup(html, "html.parser")
        node = soup.select_one(selector)
        if node is None:
            return ""
        return node.get_text(separator="\n", strip=True)

    try:
        import trafilatura

        result = trafilatura.extract(html)
        return result or ""
    except ImportError:
        return ""
