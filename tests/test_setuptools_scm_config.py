import tomllib
from pathlib import Path


def _scm_config() -> dict:
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("setuptools_scm", {})


def test_local_scheme_is_no_local_version():
    assert _scm_config().get("local_scheme") == "no-local-version"


def test_version_scheme_is_guess_next_dev():
    assert _scm_config().get("version_scheme") == "guess-next-dev"


def test_fallback_version_is_0_0_0():
    assert _scm_config().get("fallback_version") == "0.0.0"
