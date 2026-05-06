import dataclasses
import pathlib
import textwrap

import pytest

from application_pipeline import Config, ConfigError, SourceEntry, load


REQUIRED_BODY = textwrap.dedent(
    """
    from application_pipeline import SourceEntry

    KEYWORDS = ["python"]
    SKILLS = ["python"]
    SOURCES = [SourceEntry(parser_type="bundesagentur")]
    LOCATIONS = ["Hamburg"]
    """
)


def write_config(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "config.py"
    path.write_text(textwrap.dedent(body))
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "classify_relevance.md").write_text("classify\n")
    (prompts / "judge_match.md").write_text("judge\n")
    return path


def test_load_returns_populated_config(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python", "ml engineer"]
        SKILLS = ["python", "pytorch"]
        SOURCES = [SourceEntry(parser_type="bundesagentur", max_results=500)]
        LOCATIONS = ["Hamburg"]
        """,
    )

    config = load(path)

    assert isinstance(config, Config)
    assert config.keywords == ["python", "ml engineer"]
    assert config.skills == ["python", "pytorch"]
    assert config.sources == [SourceEntry(parser_type="bundesagentur", max_results=500)]
    assert config.locations == ["Hamburg"]


@pytest.mark.parametrize("missing", ["KEYWORDS", "SKILLS", "SOURCES", "LOCATIONS"])
def test_load_raises_when_required_field_missing(
    tmp_path: pathlib.Path, missing: str
) -> None:
    fields = {
        "KEYWORDS": '["python"]',
        "SKILLS": '["python"]',
        "SOURCES": "[]",
        "LOCATIONS": '["Hamburg"]',
    }
    del fields[missing]
    body = "\n".join(f"{name} = {value}" for name, value in fields.items())
    path = write_config(tmp_path, body)

    with pytest.raises(ConfigError, match=missing):
        load(path)


def test_source_entry_is_frozen() -> None:
    entry = SourceEntry(parser_type="bundesagentur", max_results=1000)
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.parser_type = "other"  # type: ignore[misc]


def test_config_is_frozen() -> None:
    config = Config(keywords=[], skills=[], sources=[], locations=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.keywords = ["x"]  # type: ignore[misc]


def test_source_entry_max_results_defaults_to_1000() -> None:
    entry = SourceEntry(parser_type="bundesagentur")
    assert entry.max_results == 1000


def test_load_defaults_when_optional_fields_absent(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.include_remote is False
    assert (
        config.classify_relevance_prompt
        == tmp_path / "prompts" / "classify_relevance.md"
    )
    assert config.judge_match_prompt == tmp_path / "prompts" / "judge_match.md"


def test_load_reads_include_remote_when_set(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY + "\nINCLUDE_REMOTE = True\n",
    )

    config = load(path)

    assert config.include_remote is True


def test_load_resolves_relative_prompt_paths_against_config_dir(
    tmp_path: pathlib.Path,
) -> None:
    settings = tmp_path / "settings"
    settings.mkdir()
    custom = settings / "custom"
    custom.mkdir()
    (custom / "relevance.md").write_text("rel\n")
    (custom / "match.md").write_text("match\n")
    path = write_config(
        settings,
        REQUIRED_BODY
        + "\nimport pathlib\n"
        + 'CLASSIFY_RELEVANCE_PROMPT = pathlib.Path("custom/relevance.md")\n'
        + 'JUDGE_MATCH_PROMPT = pathlib.Path("custom/match.md")\n',
    )

    config = load(path)

    assert config.classify_relevance_prompt == settings / "custom" / "relevance.md"
    assert config.judge_match_prompt == settings / "custom" / "match.md"


def test_load_passes_absolute_prompt_paths_through(tmp_path: pathlib.Path) -> None:
    abs_relevance = tmp_path / "elsewhere_relevance.md"
    abs_relevance.write_text("rel\n")
    abs_match = tmp_path / "elsewhere_match.md"
    abs_match.write_text("match\n")
    path = write_config(
        tmp_path,
        REQUIRED_BODY
        + "\nimport pathlib\n"
        + f'CLASSIFY_RELEVANCE_PROMPT = pathlib.Path(r"{abs_relevance}")\n'
        + f'JUDGE_MATCH_PROMPT = pathlib.Path(r"{abs_match}")\n',
    )

    config = load(path)

    assert config.classify_relevance_prompt == abs_relevance
    assert config.judge_match_prompt == abs_match


def test_load_raises_when_prompt_file_missing(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)
    (tmp_path / "prompts" / "classify_relevance.md").unlink()

    expected_path = tmp_path / "prompts" / "classify_relevance.md"
    with pytest.raises(ConfigError) as exc_info:
        load(path)
    message = str(exc_info.value)
    assert "CLASSIFY_RELEVANCE_PROMPT" in message
    assert str(expected_path) in message


def test_load_raises_when_prompt_file_empty(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)
    empty = tmp_path / "prompts" / "judge_match.md"
    empty.write_text("")

    with pytest.raises(ConfigError) as exc_info:
        load(path)
    message = str(exc_info.value)
    assert "JUDGE_MATCH_PROMPT" in message
    assert str(empty) in message


def test_load_picks_up_changed_file_on_second_call(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["first"]
        SKILLS = []
        SOURCES = [SourceEntry(parser_type="bundesagentur")]
        LOCATIONS = ["Hamburg"]
        """,
    )
    first = load(path)
    assert first.keywords == ["first"]

    write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["second"]
        SKILLS = []
        SOURCES = [SourceEntry(parser_type="bundesagentur")]
        LOCATIONS = ["Hamburg"]
        """,
    )
    second = load(path)
    assert second.keywords == ["second"]


@pytest.mark.parametrize("empty_field", ["KEYWORDS", "SOURCES", "LOCATIONS"])
def test_load_raises_when_required_list_is_empty(
    tmp_path: pathlib.Path, empty_field: str
) -> None:
    fields = {
        "KEYWORDS": '["python"]',
        "SKILLS": '["python"]',
        "SOURCES": '[SourceEntry(parser_type="bundesagentur")]',
        "LOCATIONS": '["Hamburg"]',
    }
    fields[empty_field] = "[]"
    body = "from application_pipeline import SourceEntry\n" + "\n".join(
        f"{name} = {value}" for name, value in fields.items()
    )
    path = write_config(tmp_path, body)

    with pytest.raises(ConfigError, match=empty_field):
        load(path)


def test_load_accepts_empty_skills(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python"]
        SKILLS = []
        SOURCES = [SourceEntry(parser_type="bundesagentur")]
        LOCATIONS = ["Hamburg"]
        """,
    )

    config = load(path)

    assert config.skills == []


@pytest.mark.parametrize("field", ["KEYWORDS", "SKILLS", "LOCATIONS"])
def test_load_raises_on_duplicate_strings(tmp_path: pathlib.Path, field: str) -> None:
    fields = {
        "KEYWORDS": '["python"]',
        "SKILLS": '["python"]',
        "SOURCES": '[SourceEntry(parser_type="bundesagentur")]',
        "LOCATIONS": '["Hamburg"]',
    }
    fields[field] = '["dup", "dup"]'
    body = "from application_pipeline import SourceEntry\n" + "\n".join(
        f"{name} = {value}" for name, value in fields.items()
    )
    path = write_config(tmp_path, body)

    with pytest.raises(ConfigError, match=field) as exc_info:
        load(path)
    assert "dup" in str(exc_info.value)


def test_load_raises_on_duplicate_parser_type(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python"]
        SKILLS = []
        SOURCES = [
            SourceEntry(parser_type="bundesagentur"),
            SourceEntry(parser_type="bundesagentur", max_results=10),
        ]
        LOCATIONS = ["Hamburg"]
        """,
    )

    with pytest.raises(ConfigError, match="bundesagentur"):
        load(path)


@pytest.mark.parametrize("bad_parser_type", ["", "   "])
def test_source_entry_rejects_empty_parser_type(bad_parser_type: str) -> None:
    with pytest.raises(ConfigError, match="parser_type"):
        SourceEntry(parser_type=bad_parser_type, max_results=10)


@pytest.mark.parametrize("bad_max_results", [0, -1])
def test_source_entry_rejects_non_positive_max_results(
    bad_max_results: int,
) -> None:
    with pytest.raises(ConfigError, match="max_results"):
        SourceEntry(parser_type="bundesagentur", max_results=bad_max_results)


def test_inclusion_and_negative_keywords_default_to_empty(
    tmp_path: pathlib.Path,
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.inclusion_keywords == []
    assert config.negative_keywords == []


def test_load_reads_inclusion_and_negative_keywords(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY
        + "\nINCLUSION_KEYWORDS = ['Python', 'Data Science']\nNEGATIVE_KEYWORDS = ['Pflege', 'Reinigung']\n",
    )

    config = load(path)

    assert config.inclusion_keywords == ["Python", "Data Science"]
    assert config.negative_keywords == ["Pflege", "Reinigung"]


@pytest.mark.parametrize("field", ["INCLUSION_KEYWORDS", "NEGATIVE_KEYWORDS"])
def test_load_raises_when_keyword_entry_too_short(
    tmp_path: pathlib.Path, field: str
) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY + f"\n{field} = ['ab']\n",
    )

    with pytest.raises(ConfigError, match=field):
        load(path)


@pytest.mark.parametrize("field", ["INCLUSION_KEYWORDS", "NEGATIVE_KEYWORDS"])
def test_load_raises_on_duplicate_keyword_entries(
    tmp_path: pathlib.Path, field: str
) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY + f"\n{field} = ['Python', 'Python']\n",
    )

    with pytest.raises(ConfigError, match=field):
        load(path)


def test_load_ignores_unknown_top_level_names(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY + "\nFOO = 42\n",
    )

    config = load(path)

    assert config.keywords == ["python"]


def test_load_raises_config_error_when_path_missing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "nope.py"

    with pytest.raises(ConfigError, match=str(missing)):
        load(missing)


def test_load_raises_config_error_when_path_is_directory(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(ConfigError, match=str(tmp_path)):
        load(tmp_path)


def test_load_wraps_syntax_error(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, "def broken(:\n")

    with pytest.raises(ConfigError, match=str(path.resolve())):
        load(path)


def test_load_wraps_import_error(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, "import nonexistent_module_xyz\n")

    with pytest.raises(ConfigError, match=str(path.resolve())) as exc_info:
        load(path)
    assert "nonexistent_module_xyz" in str(exc_info.value)


def test_load_wraps_arbitrary_exception(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, "x = 1 / 0\n")

    with pytest.raises(ConfigError, match=str(path.resolve())):
        load(path)


def test_load_passes_unknown_parser_type_through(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python"]
        SKILLS = []
        SOURCES = [SourceEntry(parser_type="not_a_real_parser")]
        LOCATIONS = ["Hamburg"]
        """,
    )

    config = load(path)

    assert config.sources == [SourceEntry(parser_type="not_a_real_parser")]
