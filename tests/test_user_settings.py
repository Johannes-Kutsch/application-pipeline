import pathlib
import re
import types

import pytest

from application_pipeline import ConfigError, UserSettingsError, load_user_module


def test_config_error_is_user_settings_error() -> None:
    assert issubclass(ConfigError, UserSettingsError)


def test_user_settings_error_is_exception() -> None:
    assert issubclass(UserSettingsError, Exception)


def test_load_user_module_returns_module(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "mod.py"
    path.write_text("X = 42\n")

    module = load_user_module(path, UserSettingsError)

    assert isinstance(module, types.ModuleType)
    assert module.X == 42  # type: ignore[attr-defined]


def test_load_user_module_raises_when_file_missing(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "missing.py"

    with pytest.raises(UserSettingsError, match=re.escape(str(path.resolve()))):
        load_user_module(path, UserSettingsError)


def test_load_user_module_raises_when_path_is_directory(tmp_path: pathlib.Path) -> None:
    with pytest.raises(UserSettingsError, match=re.escape(str(tmp_path.resolve()))):
        load_user_module(tmp_path, UserSettingsError)


def test_load_user_module_raises_on_syntax_error(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "bad.py"
    path.write_text("def broken(:\n")

    with pytest.raises(UserSettingsError, match=re.escape(str(path.resolve()))):
        load_user_module(path, UserSettingsError)


def test_load_user_module_raises_on_import_error(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "bad.py"
    path.write_text("import nonexistent_module_xyz\n")

    with pytest.raises(UserSettingsError, match="nonexistent_module_xyz"):
        load_user_module(path, UserSettingsError)


def test_load_user_module_raises_on_arbitrary_exception(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "bad.py"
    path.write_text("x = 1 / 0\n")

    with pytest.raises(UserSettingsError, match=re.escape(str(path.resolve()))):
        load_user_module(path, UserSettingsError)


def test_load_user_module_picks_up_changes_on_second_call(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "mod.py"
    path.write_text("VALUE = 'first'\n")

    first = load_user_module(path, UserSettingsError)
    assert first.VALUE == "first"  # type: ignore[attr-defined]

    path.write_text("VALUE = 'second'\n")

    second = load_user_module(path, UserSettingsError)
    assert second.VALUE == "second"  # type: ignore[attr-defined]


def test_load_user_module_uses_provided_error_class(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "missing.py"

    with pytest.raises(ConfigError):
        load_user_module(path, ConfigError)
