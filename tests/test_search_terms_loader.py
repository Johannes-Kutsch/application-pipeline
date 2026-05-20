from __future__ import annotations

import dataclasses
import pathlib
import textwrap

import pytest

from application_pipeline.init_cmd import init
from application_pipeline.search_terms import (
    SearchTerms,
    SearchTermsError,
    load_search_terms,
)


def write_search_terms(tmp_path: pathlib.Path, content: str) -> pathlib.Path:
    user_info = tmp_path / "user-info"
    user_info.mkdir(exist_ok=True)
    (user_info / "search-terms.md").write_text(textwrap.dedent(content))
    return user_info


def test_valid_file_loads_all_three_sections(tmp_path: pathlib.Path) -> None:
    user_info = write_search_terms(
        tmp_path,
        """
        ## Keywords

        - software engineer
        - python developer

        ## Skills

        - Python
        - SQL

        ## Negative Keywords

        - Manager
        - Sales
        """,
    )

    result = load_search_terms(user_info)

    assert isinstance(result, SearchTerms)
    assert result.keywords == ("software engineer", "python developer")
    assert result.skills == ("Python", "SQL")
    assert result.negative_keywords == ("Manager", "Sales")


def test_missing_skills_section_gives_empty_tuple(tmp_path: pathlib.Path) -> None:
    user_info = write_search_terms(
        tmp_path,
        """
        ## Keywords

        - python developer

        ## Negative Keywords

        - Manager
        """,
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
        """
        ## Keywords

        - python developer

        ## Skills

        - Python
        """,
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
        """
        ## Keywords

        ## Skills

        - Python
        """,
    )

    with pytest.raises(SearchTermsError):
        load_search_terms(user_info)


def test_missing_file_raises_search_terms_error_with_path(
    tmp_path: pathlib.Path,
) -> None:
    user_info = tmp_path / "user-info"
    user_info.mkdir()

    with pytest.raises(SearchTermsError, match="search-terms.md"):
        load_search_terms(user_info)


def test_single_char_negative_keywords_load_successfully(
    tmp_path: pathlib.Path,
) -> None:
    user_info = write_search_terms(
        tmp_path,
        """
        ## Keywords

        - python developer

        ## Negative Keywords

        - x
        - y
        """,
    )

    result = load_search_terms(user_info)

    assert result.negative_keywords == ("x", "y")


def test_search_terms_is_frozen(tmp_path: pathlib.Path) -> None:
    user_info = write_search_terms(
        tmp_path,
        """
        ## Keywords

        - python developer
        """,
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
