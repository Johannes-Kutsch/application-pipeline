import dataclasses
import pathlib
import re
import textwrap

import pytest

from application_pipeline import Config, ConfigError, SourceEntry, load
from application_pipeline.config import resolve_data_paths


REQUIRED_BODY = textwrap.dedent(
    """
    from application_pipeline import SourceEntry

    SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
    LOCATIONS = ["Hamburg"]
    """
)


def write_config(
    tmp_path: pathlib.Path, body: str, *, with_layout: bool = False
) -> pathlib.Path:
    path = tmp_path / "config.py"
    path.write_text(textwrap.dedent(body))
    if with_layout:
        (tmp_path / "layout.py").write_text("# layout stub\n")
    (tmp_path / "user-info").mkdir(exist_ok=True)
    return path


def test_load_returns_populated_config(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert isinstance(config, Config)
    assert config.sources == [SourceEntry(parser_type="bundesagentur_api")]
    assert config.locations == ["Hamburg"]


@pytest.mark.parametrize("missing", ["SOURCES", "LOCATIONS"])
def test_load_raises_when_required_field_missing(
    tmp_path: pathlib.Path, missing: str
) -> None:
    fields = {
        "SOURCES": "[]",
        "LOCATIONS": '["Hamburg"]',
    }
    del fields[missing]
    body = "\n".join(f"{name} = {value}" for name, value in fields.items())
    path = write_config(tmp_path, body)

    with pytest.raises(ConfigError, match=missing):
        load(path)


def test_source_entry_is_frozen() -> None:
    entry = SourceEntry(parser_type="bundesagentur_api")
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.parser_type = "other"  # type: ignore[misc]


def test_config_is_frozen() -> None:
    config = Config(sources=[], locations=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.sources = []  # type: ignore[misc]


def test_source_entry_has_no_max_results_field() -> None:
    field_names = {f.name for f in dataclasses.fields(SourceEntry)}
    assert "max_results" not in field_names


def test_config_has_no_claude_cli_path_field() -> None:
    field_names = {f.name for f in dataclasses.fields(Config)}
    assert "claude_cli_path" not in field_names


def test_config_has_no_agent_runtime_fields() -> None:
    field_names = {f.name for f in dataclasses.fields(Config)}
    for name in (
        "agent_runtime_service",
        "agent_runtime_model",
        "agent_runtime_effort",
        "agent_runtime_tool_policy",
    ):
        assert name not in field_names


def test_config_uses_backend_neutral_classify_field_names() -> None:
    field_names = {f.name for f in dataclasses.fields(Config)}

    assert "classify_parallelism" in field_names
    assert "classify_batch_size" in field_names
    assert "claude_classify_parallelism" not in field_names
    assert "claude_classify_batch_size" not in field_names


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

    assert config.seen_store_path == tmp_path / ".runtime-data" / "seen.json"


def test_seen_store_path_env_var_has_no_effect(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEEN_STORE_PATH", "/tmp/ignored.json")
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.seen_store_path == tmp_path / ".runtime-data" / "seen.json"


def test_load_raises_when_seen_store_path_defined_in_config(
    tmp_path: pathlib.Path,
) -> None:
    path = write_config(
        tmp_path, REQUIRED_BODY + '\nSEEN_STORE_PATH = "/tmp/store.json"\n'
    )

    with pytest.raises(ConfigError, match="SEEN_STORE_PATH"):
        load(path)


# --- data-anchored paths ---


def test_load_anchors_data_paths_to_config_dir(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.results_dir == tmp_path / "results"
    assert config.failures_path == tmp_path / ".runtime-data" / "failures"
    assert config.logs_path == tmp_path / ".runtime-data" / "logs"


def test_resolve_data_paths_anchors_to_data_dir() -> None:
    paths = resolve_data_paths(pathlib.Path("/some/data"))

    assert paths.seen_store_path == pathlib.Path("/some/data/.runtime-data/seen.json")
    assert paths.results_dir == pathlib.Path("/some/data/results")
    assert paths.failures_path == pathlib.Path("/some/data/.runtime-data/failures")
    assert paths.logs_path == pathlib.Path("/some/data/.runtime-data/logs")


# --- layout ---


def test_layout_is_none_regardless_of_sibling(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY, with_layout=True)

    config = load(path)

    assert config.layout is None


def test_layout_loads_without_sibling_layout_py(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY, with_layout=False)

    config = load(path)

    assert config.layout is None


def test_layout_knob_raises_when_set_to_none(tmp_path: pathlib.Path) -> None:
    config_path = tmp_path / "config.py"
    config_path.write_text(
        textwrap.dedent("""
            from application_pipeline import SourceEntry
            SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
            LOCATIONS = ["Hamburg"]
            LAYOUT = None
        """)
    )
    (tmp_path / "user-info").mkdir()

    with pytest.raises(ConfigError, match="LAYOUT is no longer supported"):
        load(config_path)


def test_layout_knob_raises_when_set_to_path(tmp_path: pathlib.Path) -> None:
    layout_file = tmp_path / "layout.py"
    layout_file.write_text("# layout\n")
    config_path = tmp_path / "config.py"
    config_path.write_text(
        textwrap.dedent(f"""
            import pathlib
            from application_pipeline import SourceEntry
            SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
            LOCATIONS = ["Hamburg"]
            LAYOUT = pathlib.Path(r"{layout_file}")
        """)
    )
    (tmp_path / "user-info").mkdir()

    with pytest.raises(ConfigError, match="LAYOUT is no longer supported"):
        load(config_path)


# --- user_info_dir ---


def test_user_info_dir_raises_when_set_in_config(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY
        + "\nimport pathlib\nUSER_INFO_DIR = pathlib.Path('/tmp/custom')\n",
    )

    with pytest.raises(ConfigError, match="USER_INFO_DIR is no longer supported"):
        load(path)


def test_user_info_dir_defaults_to_user_info_subdir(tmp_path: pathlib.Path) -> None:
    user_info = tmp_path / "user-info"
    user_info.mkdir()
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.user_info_dir == tmp_path / "user-info"


def test_user_info_dir_raises_when_canonical_dir_missing(
    tmp_path: pathlib.Path,
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)
    (tmp_path / "user-info").rmdir()

    with pytest.raises(ConfigError, match="USER_INFO_DIR"):
        load(path)


# --- prompt knobs retired ---


def test_load_raises_when_classify_relevance_prompt_set(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path, REQUIRED_BODY + '\nCLASSIFY_RELEVANCE_PROMPT = "anything"\n'
    )

    with pytest.raises(
        ConfigError, match="CLASSIFY_RELEVANCE_PROMPT is no longer supported"
    ):
        load(path)


def test_load_raises_when_judge_match_prompt_set(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + '\nJUDGE_MATCH_PROMPT = "anything"\n')

    with pytest.raises(ConfigError, match="JUDGE_MATCH_PROMPT is no longer supported"):
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


def test_load_raises_when_claude_cli_path_present(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path, REQUIRED_BODY + '\nCLAUDE_CLI_PATH = "/usr/bin/claude"\n'
    )

    with pytest.raises(ConfigError, match="CLAUDE_CLI_PATH is no longer supported"):
        load(path)


def test_load_raises_when_claude_classify_parallelism_present(
    tmp_path: pathlib.Path,
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + "\nCLAUDE_CLASSIFY_PARALLELISM = 4\n")

    with pytest.raises(
        ConfigError, match="CLAUDE_CLASSIFY_PARALLELISM is no longer supported"
    ):
        load(path)


def test_load_raises_when_claude_classify_batch_size_present(
    tmp_path: pathlib.Path,
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + "\nCLAUDE_CLASSIFY_BATCH_SIZE = 10\n")

    with pytest.raises(
        ConfigError, match="CLAUDE_CLASSIFY_BATCH_SIZE is no longer supported"
    ):
        load(path)


def test_load_picks_up_changed_file_on_second_call(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
        LOCATIONS = ["Hamburg"]
        """,
    )
    first = load(path)
    assert first.locations == ["Hamburg"]

    write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
        LOCATIONS = ["Berlin"]
        """,
    )
    second = load(path)
    assert second.locations == ["Berlin"]


def test_load_raises_when_sources_is_empty(tmp_path: pathlib.Path) -> None:
    body = (
        "from application_pipeline import SourceEntry\n"
        "SOURCES = []\n"
        'LOCATIONS = ["Hamburg"]\n'
    )
    path = write_config(tmp_path, body)

    with pytest.raises(ConfigError, match="SOURCES"):
        load(path)


def test_empty_locations_with_include_remote_false_raises(
    tmp_path: pathlib.Path,
) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

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

        SOURCES = [SourceEntry(parser_type="bundesagentur_api")]
        LOCATIONS = []
        INCLUDE_REMOTE = True
        """,
    )

    config = load(path)

    assert config.locations == []
    assert config.include_remote is True


def test_load_raises_on_duplicate_locations(tmp_path: pathlib.Path) -> None:
    body = (
        "from application_pipeline import SourceEntry\n"
        'SOURCES = [SourceEntry(parser_type="bundesagentur_api")]\n'
        'LOCATIONS = ["dup", "dup"]\n'
    )
    path = write_config(tmp_path, body)

    with pytest.raises(ConfigError, match="LOCATIONS") as exc_info:
        load(path)
    assert "dup" in str(exc_info.value)


def test_load_raises_on_duplicate_parser_type(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        SOURCES = [
            SourceEntry(parser_type="bundesagentur_api"),
            SourceEntry(parser_type="bundesagentur_api"),
        ]
        LOCATIONS = ["Hamburg"]
        """,
    )

    with pytest.raises(ConfigError, match="bundesagentur"):
        load(path)


@pytest.mark.parametrize("bad_parser_type", ["", "   "])
def test_source_entry_rejects_empty_parser_type(bad_parser_type: str) -> None:
    with pytest.raises(ConfigError, match="parser_type"):
        SourceEntry(parser_type=bad_parser_type)


def test_legacy_keywords_silently_ignored(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY
        + "\nKEYWORDS = ['python']\nSKILLS = ['Python']\nNEGATIVE_KEYWORDS = ['Pflege']\n",
    )

    config = load(path)

    assert not hasattr(config, "keywords")
    assert not hasattr(config, "skills")
    assert not hasattr(config, "negative_keywords")


def test_load_silently_ignores_inclusion_keywords(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY + "\nINCLUSION_KEYWORDS = ['Python', 'Data Science']\n",
    )

    import logging

    with caplog.at_level(logging.INFO):
        config = load(path)

    assert not hasattr(config, "inclusion_keywords")
    assert any("INCLUSION_KEYWORDS" in r.message for r in caplog.records)
    assert any("ADR-0026" in r.message for r in caplog.records)


def test_load_ignores_unknown_top_level_names(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        REQUIRED_BODY + "\nFOO = 42\n",
    )

    config = load(path)

    assert config.locations == ["Hamburg"]


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


# --- MAX_LISTING_AGE_DAYS ---


def test_max_listing_age_days_defaults_to_180(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.max_listing_age_days == 180


def test_max_listing_age_days_accepts_explicit_value(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + "\nMAX_LISTING_AGE_DAYS = 30\n")

    config = load(path)

    assert config.max_listing_age_days == 30


@pytest.mark.parametrize("value", [0, -1, -100])
def test_max_listing_age_days_raises_when_less_than_1(
    tmp_path: pathlib.Path, value: int
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + f"\nMAX_LISTING_AGE_DAYS = {value}\n")

    with pytest.raises(ConfigError, match="MAX_LISTING_AGE_DAYS"):
        load(path)


@pytest.mark.parametrize("value", ['"90"', "3.5", "True"])
def test_max_listing_age_days_raises_when_non_int(
    tmp_path: pathlib.Path, value: str
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + f"\nMAX_LISTING_AGE_DAYS = {value}\n")

    with pytest.raises(ConfigError, match="MAX_LISTING_AGE_DAYS"):
        load(path)


# --- classify_parallelism ---


def test_classify_parallelism_defaults_to_4(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.classify_parallelism == 4


@pytest.mark.parametrize("value", [0, -1])
def test_classify_parallelism_raises_when_less_than_1(
    tmp_path: pathlib.Path, value: int
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + f"\nCLASSIFY_PARALLELISM = {value}\n")

    with pytest.raises(ConfigError, match="CLASSIFY_PARALLELISM"):
        load(path)


@pytest.mark.parametrize("value", ['"4"', "4.0", "True", "False"])
def test_classify_parallelism_raises_when_non_int(
    tmp_path: pathlib.Path, value: str
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + f"\nCLASSIFY_PARALLELISM = {value}\n")

    with pytest.raises(ConfigError, match="CLASSIFY_PARALLELISM"):
        load(path)


def test_classify_parallelism_accepts_1(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + "\nCLASSIFY_PARALLELISM = 1\n")

    config = load(path)

    assert config.classify_parallelism == 1


# --- classify_batch_size ---


def test_classify_batch_size_defaults_to_10(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.classify_batch_size == 10


def test_classify_batch_size_reads_configured_value(
    tmp_path: pathlib.Path,
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + "\nCLASSIFY_BATCH_SIZE = 5\n")

    config = load(path)

    assert config.classify_batch_size == 5


def test_classify_batch_size_accepts_1(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + "\nCLASSIFY_BATCH_SIZE = 1\n")

    config = load(path)

    assert config.classify_batch_size == 1


@pytest.mark.parametrize("value", [0, -1])
def test_classify_batch_size_raises_when_less_than_1(
    tmp_path: pathlib.Path, value: int
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + f"\nCLASSIFY_BATCH_SIZE = {value}\n")

    with pytest.raises(ConfigError, match="CLASSIFY_BATCH_SIZE"):
        load(path)


@pytest.mark.parametrize("value", ["'10'", "10.0", "True", "False"])
def test_classify_batch_size_raises_when_not_int(
    tmp_path: pathlib.Path, value: str
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + f"\nCLASSIFY_BATCH_SIZE = {value}\n")

    with pytest.raises(ConfigError, match="CLASSIFY_BATCH_SIZE"):
        load(path)


# --- dedup_cooldown_days ---


def test_dedup_cooldown_days_defaults_to_30(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY)

    config = load(path)

    assert config.dedup_cooldown_days == 30


def test_dedup_cooldown_days_reads_configured_value(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + "\nDEDUP_COOLDOWN_DAYS = 14\n")

    config = load(path)

    assert config.dedup_cooldown_days == 14


def test_dedup_cooldown_days_accepts_1(tmp_path: pathlib.Path) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + "\nDEDUP_COOLDOWN_DAYS = 1\n")

    config = load(path)

    assert config.dedup_cooldown_days == 1


@pytest.mark.parametrize("value", [0, -1])
def test_dedup_cooldown_days_raises_when_less_than_1(
    tmp_path: pathlib.Path, value: int
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + f"\nDEDUP_COOLDOWN_DAYS = {value}\n")

    with pytest.raises(ConfigError, match="DEDUP_COOLDOWN_DAYS"):
        load(path)


@pytest.mark.parametrize("value", ["'30'", "30.0", "True", "False"])
def test_dedup_cooldown_days_raises_when_not_int(
    tmp_path: pathlib.Path, value: str
) -> None:
    path = write_config(tmp_path, REQUIRED_BODY + f"\nDEDUP_COOLDOWN_DAYS = {value}\n")

    with pytest.raises(ConfigError, match="DEDUP_COOLDOWN_DAYS"):
        load(path)
