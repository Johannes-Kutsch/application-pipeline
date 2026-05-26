from __future__ import annotations

import pathlib
import re

from .types import SearchTerms, SearchTermsError

_DIR = "search-terms"
_FILE_KEYWORDS = "keywords.md"
_FILE_NEGATIVE_KEYWORDS = "negative-keywords.md"
_BULLET_RE = re.compile(r"^-\s+(.+)$")


def load_search_terms(user_info_dir: pathlib.Path) -> SearchTerms:
    base = user_info_dir / _DIR
    keywords_path = base / _FILE_KEYWORDS

    if not keywords_path.exists():
        raise SearchTermsError(f"Missing required file: {keywords_path}")

    keywords = _parse_bullets(keywords_path)
    if not keywords:
        raise SearchTermsError(
            f"{keywords_path}: file is present but contains no bullet entries"
        )

    return SearchTerms(
        keywords=tuple(keywords),
        negative_keywords=tuple(_parse_optional(base / _FILE_NEGATIVE_KEYWORDS)),
    )


def _parse_optional(path: pathlib.Path) -> list[str]:
    return _parse_bullets(path) if path.exists() else []


def _parse_bullets(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    result = []
    for line in text.splitlines():
        m = _BULLET_RE.match(line)
        if m:
            result.append(m.group(1).strip())
    return result
