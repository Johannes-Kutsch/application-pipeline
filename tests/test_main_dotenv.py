"""Tests that __main__ loads CLAUDE_CODE_OAUTH_TOKEN from ~/.env into os.environ."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


def test_token_loaded_from_home_env_file(tmp_path):
    (tmp_path / ".env").write_text("CLAUDE_CODE_OAUTH_TOKEN=test-token-from-file\n")
    base_env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}
    assert _run_probe(tmp_path, base_env) == "test-token-from-file"


def test_shell_exported_token_wins_over_file(tmp_path):
    (tmp_path / ".env").write_text("CLAUDE_CODE_OAUTH_TOKEN=from-file\n")
    base_env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": "from-shell"}
    assert _run_probe(tmp_path, base_env) == "from-shell"


def test_missing_env_file_is_silent(tmp_path):
    base_env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}
    assert _run_probe(tmp_path, base_env) == "<missing>"
