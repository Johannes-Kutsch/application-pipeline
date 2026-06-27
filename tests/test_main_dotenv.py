"""Tests that __main__ does not import process env from ~/.env at import time."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Build a PYTHONPATH that makes application_pipeline and its dependencies
# importable in a subprocess regardless of what HOME is set to.
_PYTHONPATH = os.pathsep.join(
    p
    for p in sys.path
    if p  # exclude empty string (cwd)
)


def _run_probe(home_dir: Path, env: dict[str, str]) -> str:
    """Run a subprocess that imports __main__ and prints the token value."""
    script = (
        "import os, sys; "
        "import application_pipeline.__main__; "
        "print(os.environ.get('CLAUDE_CODE_OAUTH_TOKEN', '<missing>'))"
    )
    probe_env = {
        **env,
        "HOME": str(home_dir),
        "USERPROFILE": str(home_dir),
        "PYTHONPATH": _PYTHONPATH,
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=probe_env,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run_probe_for_key(home_dir: Path, env: dict[str, str], key: str) -> str:
    script = (
        "import os, sys; "
        "import application_pipeline.__main__; "
        f"print(os.environ.get({key!r}, '<missing>'))"
    )
    probe_env = {
        **env,
        "HOME": str(home_dir),
        "USERPROFILE": str(home_dir),
        "PYTHONPATH": _PYTHONPATH,
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=probe_env,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CLAUDE_CODE_OAUTH_TOKEN", "test-token-from-file"),
        ("OPENCODE_GO_API_KEY", "test-opencode-key-from-file"),
    ],
)
def test_home_env_file_does_not_populate_process_env_keys(
    tmp_path: Path, key: str, value: str
) -> None:
    (tmp_path / ".env").write_text(f"{key}={value}\n")
    base_env = {k: v for k, v in os.environ.items() if k != key}
    assert _run_probe_for_key(tmp_path, base_env, key) == "<missing>"


@pytest.mark.parametrize(
    ("key", "shell_value"),
    [
        ("CLAUDE_CODE_OAUTH_TOKEN", "from-shell"),
        ("OPENCODE_GO_API_KEY", "opencode-from-shell"),
    ],
)
def test_shell_exported_env_value_remains_unchanged_on_import(
    tmp_path: Path, key: str, shell_value: str
) -> None:
    (tmp_path / ".env").write_text(f"{key}=from-file\n")
    base_env = {**os.environ, key: shell_value}
    assert _run_probe_for_key(tmp_path, base_env, key) == shell_value


def test_missing_env_file_is_silent(tmp_path):
    base_env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}
    assert _run_probe(tmp_path, base_env) == "<missing>"
