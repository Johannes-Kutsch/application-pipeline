import dataclasses
import pathlib
import re
import textwrap

import pytest

from application_pipeline import Config, ConfigError, SourceEntry, load


REQUIRED_BODY = textwrap.dedent(
    """
    from application_pipeline import SourceEntry

    KEYWORDS = ["python"]
    SKILLS = ["python"]
    SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
    LOCATIONS = ["Hamburg"]
    """
)


def write_config(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "config.py"
    path.write_text(textwrap.dedent(body))
    (tmp_path / "user-info").mkdir(exist_ok=True)
    return path


def test_load_returns_populated_config(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python", "ml engineer"]
        SKILLS = ["python", "pytorch"]
        SOURCES = [SourceEntry(parser_type="bundesagentur_api", max_results=500)]
        LOCATIONS = ["Hamburg"]
        """,
    )

    config = load(path)

    assert isinstance(config, Config)
    assert config.keywords == ["python", "ml engineer"]
    assert config.skills == ["python", "pytorch"]
    assert config.sources == [
        SourceEntry(parser_type="bundesagentur_api", max_results=500)
    ]
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
    entry = SourceEntry(parser_type="bundesagentur_api", max_results=1000)
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.parser_type = "other"  # type: ignore[misc]


def test_config_is_frozen() -> None:
    config = Config(keywords=[], skills=[], sources=[], locations=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.keywords = ["x"]  # type: ignore[misc]


def test_source_entry_max_results_defaults_to_1000() -> None:
    entry = SourceEntry(parser_type="bundesagentur_api")
    assert entry.max_results == 1000


def test_load_defaults_when_optional_fields_absent(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.include_remote is True


def test_include_remote_can_be_set_to_false(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY + "\nINCLUDE_REMOTE = False\n",
    )

    config = load(path)

    assert config.include_remote is False


def test_load_reads_include_remote_when_set(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY + "\nINCLUDE_REMOTE = True\n",
    )

    config = load(path)

    assert config.include_remote is True


# --- seen_store_path ---


def test_seen_store_path_defaults_to_seen_json(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.seen_store_path == tmp_path / ".seen.json"


def test_seen_store_path_read_from_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom.json"
    monkeypatch.setenv("SEEN_STORE_PATH", str(custom))
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.seen_store_path == custom


def test_seen_store_path_coerces_string_from_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEEN_STORE_PATH", "/tmp/store.json")
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert isinstance(config.seen_store_path, pathlib.Path)
    assert config.seen_store_path == pathlib.Path("/tmp/store.json")


def test_seen_store_path_no_existence_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonexistent = tmp_path / "does_not_exist.json"
    monkeypatch.setenv("SEEN_STORE_PATH", str(nonexistent))
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)  # must not raise

    assert config.seen_store_path == nonexistent


# --- layout ---


def test_layout_defaults_to_none(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.layout is None


def test_layout_accepted_when_valid_file(tmp_path: pathlib.Path) -> None:
    layout_file = tmp_path / "layout.py"
    layout_file.write_text("# layout\n")
    path = write_config(
        tmp_path,
        REQUIRED_BODY + f"\nimport pathlib\nLAYOUT = pathlib.Path(r'{layout_file}')\n",
    )

    config = load(path)

    assert config.layout == layout_file


def test_layout_raises_when_file_missing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "no_layout.py"
    path = write_config(
        tmp_path,
        REQUIRED_BODY + f"\nimport pathlib\nLAYOUT = pathlib.Path(r'{missing}')\n",
    )

    with pytest.raises(ConfigError, match="LAYOUT"):
        load(path)


def test_layout_raises_when_path_is_directory(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY + f"\nimport pathlib\nLAYOUT = pathlib.Path(r'{tmp_path}')\n",
    )

    with pytest.raises(ConfigError, match="LAYOUT"):
        load(path)


# --- user_info_dir ---


def test_user_info_dir_defaults_to_user_info_subdir(tmp_path: pathlib.Path) -> None:
    user_info = tmp_path / "user-info"
    user_info.mkdir()
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.user_info_dir == tmp_path / "user-info"


def test_user_info_dir_raises_when_not_a_directory(tmp_path: pathlib.Path) -> None:
    missing_dir = tmp_path / "no_such_user_info"
    path = write_config(
        tmp_path,
        REQUIRED_BODY
        + f"\nimport pathlib\nUSER_INFO_DIR = pathlib.Path(r'{missing_dir}')\n",
    )

    with pytest.raises(ConfigError, match="USER_INFO_DIR"):
        load(path)


def test_load_resolves_relative_user_info_dir_against_config_dir(
    tmp_path: pathlib.Path,
) -> None:
    settings = tmp_path / "settings"
    settings.mkdir()
    user_info = settings / "my-user-info"
    user_info.mkdir()
    path = write_config(
        settings,
        REQUIRED_BODY
        + "\nimport pathlib\n"
        + 'USER_INFO_DIR = pathlib.Path("my-user-info")\n',
    )

    config = load(path)

    assert config.user_info_dir == settings / "my-user-info"


# --- per-prompt-file fields ---


def test_classify_relevance_prompt_defaults_to_none(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.classify_relevance_prompt is None


def test_judge_match_prompt_defaults_to_none(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.judge_match_prompt is None


def test_classify_relevance_prompt_accepted_when_valid(tmp_path: pathlib.Path) -> None:
    prompt_file = tmp_path / "classify.md"
    prompt_file.write_text("prompt content\n")
    path = write_config(
        tmp_path,
        REQUIRED_BODY
        + f"\nimport pathlib\nCLASSIFY_RELEVANCE_PROMPT = pathlib.Path(r'{prompt_file}')\n",
    )

    config = load(path)

    assert config.classify_relevance_prompt == prompt_file


def test_classify_relevance_prompt_raises_when_missing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "missing.md"
    path = write_config(
        tmp_path,
        REQUIRED_BODY
        + f"\nimport pathlib\nCLASSIFY_RELEVANCE_PROMPT = pathlib.Path(r'{missing}')\n",
    )

    with pytest.raises(ConfigError, match="CLASSIFY_RELEVANCE_PROMPT"):
        load(path)


def test_classify_relevance_prompt_raises_when_empty(tmp_path: pathlib.Path) -> None:
    empty_file = tmp_path / "empty.md"
    empty_file.write_text("")
    path = write_config(
        tmp_path,
        REQUIRED_BODY
        + f"\nimport pathlib\nCLASSIFY_RELEVANCE_PROMPT = pathlib.Path(r'{empty_file}')\n",
    )

    with pytest.raises(ConfigError, match="CLASSIFY_RELEVANCE_PROMPT"):
        load(path)


def test_judge_match_prompt_raises_when_missing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "missing.md"
    path = write_config(
        tmp_path,
        REQUIRED_BODY
        + f"\nimport pathlib\nJUDGE_MATCH_PROMPT = pathlib.Path(r'{missing}')\n",
    )

    with pytest.raises(ConfigError, match="JUDGE_MATCH_PROMPT"):
        load(path)


# --- OLLAMA_* fields rejected ---


@pytest.mark.parametrize(
    "field",
    [
        "OLLAMA_BASE_URL",
        "OLLAMA_CLASSIFY_MODEL",
        "OLLAMA_JUDGE_MODEL",
        "OLLAMA_READ_TIMEOUT_SECONDS",
        "OLLAMA_JSON_RETRIES",
        "OLLAMA_HTTP_RETRIES",
        "OLLAMA_KEEP_ALIVE",
    ],
)
def test_load_raises_when_ollama_field_present(
    tmp_path: pathlib.Path, field: str
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + f'\n{field} = "anything"\n')

    with pytest.raises(ConfigError, match=field):
        load(path)


# --- keyword normalize length check ---


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
def test_keyword_length_uses_normalized_value(
    tmp_path: pathlib.Path, field: str
) -> None:
    # "  ab  " normalizes to "ab" (length 2) — must still be rejected
    path = write_config(
        tmp_path,
        REQUIRED_BODY + f"\n{field} = ['  ab  ']\n",
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


def test_load_picks_up_changed_file_on_second_call(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["first"]
        SKILLS = []
        SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
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
        SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
        LOCATIONS = ["Hamburg"]
        """,
    )
    second = load(path)
    assert second.keywords == ["second"]


@pytest.mark.parametrize("empty_field", ["KEYWORDS", "SOURCES"])
def test_load_raises_when_required_list_is_empty(
    tmp_path: pathlib.Path, empty_field: str
) -> None:
    fields = {
        "KEYWORDS": '["python"]',
        "SKILLS": '["python"]',
        "SOURCES": '[SourceEntry(parser_type="bundesagentur_api")]',
        "LOCATIONS": '["Hamburg"]',
    }
    fields[empty_field] = "[]"
    body = "from application_pipeline import SourceEntry\n" + "\n".join(
        f"{name} = {value}" for name, value in fields.items()
    )
    path = write_config(tmp_path, body)

    with pytest.raises(ConfigError, match=empty_field):
        load(path)


def test_empty_locations_with_include_remote_false_raises(
    tmp_path: pathlib.Path,
) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python"]
        SKILLS = []
        SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
        LOCATIONS = []
        INCLUDE_REMOTE = False
        """,
    )

    with pytest.raises(ConfigError, match="nothing to search"):
        load(path)


def test_empty_locations_with_include_remote_true_is_valid(
    tmp_path: pathlib.Path,
) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python"]
        SKILLS = []
        SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
        LOCATIONS = []
        INCLUDE_REMOTE = True
        """,
    )

    config = load(path)

    assert config.locations == []
    assert config.include_remote is True


def test_load_accepts_empty_skills(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python"]
        SKILLS = []
        SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
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
        "SOURCES": '[SourceEntry(parser_type="bundesagentur_api")]',
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
            SourceEntry(parser_type="bundesagentur_api"),
            SourceEntry(parser_type="bundesagentur_api", max_results=10),
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
        SourceEntry(parser_type="bundesagentur_api", max_results=bad_max_results)


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


def test_load_ignores_unknown_top_level_names(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY + "\nFOO = 42\n",
    )

    config = load(path)

    assert config.keywords == ["python"]


def test_load_raises_config_error_when_path_missing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "nope.py"

    with pytest.raises(ConfigError, match=re.escape(str(missing))):
        load(missing)


def test_load_raises_config_error_when_path_is_directory(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(ConfigError, match=re.escape(str(tmp_path))):
        load(tmp_path)


def test_load_wraps_syntax_error(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, "def broken(:\n")

    with pytest.raises(ConfigError, match=re.escape(str(path.resolve()))):
        load(path)


def test_load_wraps_import_error(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, "import nonexistent_module_xyz\n")

    with pytest.raises(ConfigError, match=re.escape(str(path.resolve()))) as exc_info:
        load(path)
    assert "nonexistent_module_xyz" in str(exc_info.value)


def test_load_wraps_arbitrary_exception(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, "x = 1 / 0\n")

    with pytest.raises(ConfigError, match=re.escape(str(path.resolve()))):
        load(path)


def test_load_passes_unknown_parser_type_through(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python"]
        SKILLS = []
        SOURCES = [
            SourceEntry(parser_type="bundesagentur_api"),
            SourceEntry(parser_type="not_a_real_parser"),
        ]
        LOCATIONS = ["Hamburg"]
        """,
    )

    config = load(path)

    assert SourceEntry(parser_type="not_a_real_parser") in config.sources


# --- location coverage validation ---


def test_unknown_location_raises_config_error(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python"]
        SKILLS = []
        SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
        LOCATIONS = ["Atlantis"]
        """,
    )

    with pytest.raises(ConfigError, match="Atlantis"):
        load(path)


def test_include_remote_without_remote_capable_source_raises(
    tmp_path: pathlib.Path,
) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python"]
        SKILLS = []
        SOURCES = [SourceEntry(parser_type="stellen_hamburg_api")]
        LOCATIONS = ["Hamburg"]
        INCLUDE_REMOTE = True
        """,
    )

    with pytest.raises(ConfigError, match="include_remote"):
        load(path)


def test_valid_locations_and_sources_pass_silently(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["python"]
        SKILLS = []
        SOURCES = [
            SourceEntry(parser_type="bundesagentur_api"),
            SourceEntry(parser_type="stellen_hamburg_api"),
        ]
        LOCATIONS = ["Hamburg"]
        """,
    )

    config = load(path)

    assert config.locations == ["Hamburg"]
