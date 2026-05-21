from __future__ import annotations

import dataclasses
import pathlib

import pytest

from application_pipeline.init_cmd import init
from application_pipeline.search_terms import (
    SearchTerms,
    SearchTermsError,
    load_search_terms,
)


def write_search_terms(
    tmp_path: pathlib.Path,
    *,
    keywords: str = "",
    skills: str | None = None,
    negative_keywords: str | None = None,
) -> pathlib.Path:
    user_info = tmp_path / "user-info"
    st_dir = user_info / "search-terms"
    st_dir.mkdir(parents=True, exist_ok=True)
    (st_dir / "keywords.md").write_text(keywords)
    if skills is not None:
        (st_dir / "skills.md").write_text(skills)
    if negative_keywords is not None:
        (st_dir / "negative-keywords.md").write_text(negative_keywords)
    return user_info


def test_valid_file_loads_all_three_sections(tmp_path: pathlib.Path) -> None:
    user_info = write_search_terms(
        tmp_path,
        keywords="- software engineer\n- python developer\n",
        skills="- Python\n- SQL\n",
        negative_keywords="- Manager\n- Sales\n",
    )

    result = load_search_terms(user_info)

    assert isinstance(result, SearchTerms)
    assert result.keywords == ("software engineer", "python developer")
    assert result.skills == ("Python", "SQL")
    assert result.negative_keywords == ("Manager", "Sales")


def test_missing_skills_section_gives_empty_tuple(tmp_path: pathlib.Path) -> None:
    user_info = write_search_terms(
        tmp_path,
        keywords="- python developer\n",
        negative_keywords="- Manager\n",
    )

    result = load_search_terms(user_info)

    assert result.skills == ()
    assert result.keywords == ("python developer",)
    assert result.negative_keywords == ("Manager",)


def test_missing_negative_keywords_section_gives_empty_tuple(
    tmp_path: pathlib.Path,
) -> None:
    user_info = write_search_terms(
        tmp_path,
        keywords="- python developer\n",
        skills="- Python\n",
    )

    result = load_search_terms(user_info)

    assert result.negative_keywords == ()
    assert result.keywords == ("python developer",)
    assert result.skills == ("Python",)


def test_empty_keywords_section_raises_search_terms_error(
    tmp_path: pathlib.Path,
) -> None:
    user_info = write_search_terms(
        tmp_path,
        keywords="",
        skills="- Python\n",
    )

    with pytest.raises(SearchTermsError):
        load_search_terms(user_info)


def test_missing_file_raises_search_terms_error_with_path(
    tmp_path: pathlib.Path,
) -> None:
    user_info = tmp_path / "user-info"
    user_info.mkdir()

    with pytest.raises(SearchTermsError, match="keywords.md"):
        load_search_terms(user_info)


def test_single_char_negative_keywords_load_successfully(
    tmp_path: pathlib.Path,
) -> None:
    user_info = write_search_terms(
        tmp_path,
        keywords="- python developer\n",
        negative_keywords="- x\n- y\n",
    )

    result = load_search_terms(user_info)

    assert result.negative_keywords == ("x", "y")


def test_search_terms_is_frozen(tmp_path: pathlib.Path) -> None:
    user_info = write_search_terms(
        tmp_path,
        keywords="- python developer\n",
    )

    result = load_search_terms(user_info)

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.keywords = ("other",)  # type: ignore[misc]


def test_init_seeds_search_terms_template_that_loads_successfully(
    tmp_path: pathlib.Path,
) -> None:
    init(tmp_path)

    result = load_search_terms(tmp_path / "user-info")

    assert isinstance(result, SearchTerms)
    assert result.keywords
