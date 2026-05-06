import dataclasses
import pathlib
import textwrap

import pytest

from application_pipeline import Config, ConfigError, SourceEntry, load


def write_config(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "config.py"
    path.write_text(textwrap.dedent(body))
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


def test_load_picks_up_changed_file_on_second_call(tmp_path: pathlib.Path) -> None:
    path = write_config(
        tmp_path,
        """
        from application_pipeline import SourceEntry

        KEYWORDS = ["first"]
        SKILLS = []
        SOURCES = []
        LOCATIONS = []
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
        SOURCES = []
        LOCATIONS = []
        """,
    )
    second = load(path)
    assert second.keywords == ["second"]
