from __future__ import annotations

import pathlib
import re

from .types import SearchTerms, SearchTermsError

_FILENAME = "search-terms.md"
_SECTION_RE = re.compile(r"^##\s+(.+)$")
_BULLET_RE = re.compile(r"^-\s+(.+)$")

_SECTION_KEYWORDS = "keywords"
_SECTION_SKILLS = "skills"
_SECTION_NEGATIVE_KEYWORDS = "negative keywords"


def load_search_terms(user_info_dir: pathlib.Path) -> SearchTerms:
    path = user_info_dir / _FILENAME
    if not path.exists():
        raise SearchTermsError(f"Missing required file: {path}")

    text = path.read_text(encoding="utf-8-sig")
    sections = _parse_sections(text)

    keywords = sections.get(_SECTION_KEYWORDS, [])
    skills = sections.get(_SECTION_SKILLS, [])
    negative_keywords = sections.get(_SECTION_NEGATIVE_KEYWORDS, [])

    if _SECTION_KEYWORDS in sections and not keywords:
        raise SearchTermsError(
            f"{path}: ## Keywords section is present but contains no bullet entries"
        )

    return SearchTerms(
        keywords=tuple(keywords),
        skills=tuple(skills),
        negative_keywords=tuple(negative_keywords),
    )


def _parse_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for line in text.splitlines():
        header_match = _SECTION_RE.match(line)
        if header_match:
            current = header_match.group(1).strip().lower()
            sections[current] = []
            continue

        if current is not None:
            bullet_match = _BULLET_RE.match(line)
            if bullet_match:
                sections[current].append(bullet_match.group(1).strip())

    return sections
