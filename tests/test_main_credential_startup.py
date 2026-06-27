"""Tests that run/cron fail at startup when the local Operator Credential is missing."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_PYTHONPATH = os.pathsep.join(p for p in sys.path if p)


def _minimal_config(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.py").write_text(
        "from application_pipeline import SourceEntry\n"
        'SOURCES = [SourceEntry(parser_type="stellen_hamburg_api")]\n'
        'LOCATIONS = ["Hamburg"]\n'
        "INCLUDE_REMOTE = False\n",
        encoding="utf-8",
    )
    user_info = home / "user-info"
    triage = user_info / "triage-profile"
    triage.mkdir(parents=True, exist_ok=True)
    (triage / "candidate-profile.md").write_text("dev\n", encoding="utf-8")
    (triage / "gate-criteria.md").write_text("Hamburg\n", encoding="utf-8")
    search_terms = user_info / "search-terms"
    search_terms.mkdir(parents=True, exist_ok=True)
    (search_terms / "keywords.md").write_text("- python\n", encoding="utf-8")
    (search_terms / "skills.md").write_text("- python\n", encoding="utf-8")
    (search_terms / "negative-keywords.md").write_text("\n", encoding="utf-8")


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "application_pipeline", *argv],
        cwd=str(cwd),
        env={**os.environ, "PYTHONPATH": _PYTHONPATH},
        capture_output=True,
        text=True,
    )


def test_run_fails_at_startup_when_settings_env_missing(tmp_path: Path) -> None:
    """application-pipeline run exits before parser work when <settings-dir>/.env is missing."""
    home = tmp_path / "application-pipeline"
    _minimal_config(home)

    result = _run(["run"], tmp_path)

    assert result.returncode != 0
    stderr = result.stderr + result.stdout
    assert ".env" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_run_missing_operator_credential_stops_before_parser_work(
    tmp_path: Path,
) -> None:
    """A missing Operator Credential stops run before Parser Lifecycle setup writes logs."""
    home = tmp_path / "application-pipeline"
    _minimal_config(home)

    result = _run(["run"], tmp_path)

    assert result.returncode != 0
    assert not (home / ".runtime-data" / "logs").exists()


def test_run_fails_at_startup_when_operator_credential_is_empty(
    tmp_path: Path,
) -> None:
    """application-pipeline run exits before parser work when OPENCODE_GO_API_KEY is empty."""
    home = tmp_path / "application-pipeline"
    _minimal_config(home)
    (home / ".env").write_text("OPENCODE_GO_API_KEY=\n", encoding="utf-8")

    result = _run(["run"], tmp_path)

    assert result.returncode != 0
    stderr = result.stderr + result.stdout
    assert ".env" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_run_fails_at_startup_when_operator_credential_key_missing(
    tmp_path: Path,
) -> None:
    """application-pipeline run exits before parser work when OPENCODE_GO_API_KEY is absent."""
    home = tmp_path / "application-pipeline"
    _minimal_config(home)
    (home / ".env").write_text("SOME_OTHER_KEY=value\n", encoding="utf-8")

    result = _run(["run"], tmp_path)

    assert result.returncode != 0
    stderr = result.stderr + result.stdout
    assert ".env" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_run_ignores_shell_exported_operator_credential_without_settings_env(
    tmp_path: Path,
) -> None:
    """A shell-exported Operator Credential does not bypass the local settings-dir file requirement."""
    home = tmp_path / "application-pipeline"
    _minimal_config(home)

    result = subprocess.run(
        [sys.executable, "-m", "application_pipeline", "run"],
        cwd=str(tmp_path),
        env={
            **os.environ,
            "PYTHONPATH": _PYTHONPATH,
            "OPENCODE_GO_API_KEY": "from-shell",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    stderr = result.stderr + result.stdout
    assert ".env" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_cron_fails_at_startup_when_operator_credential_missing(
    tmp_path: Path,
) -> None:
    """application-pipeline cron exits before parser work when the credential is missing."""
    home = tmp_path / "application-pipeline"
    _minimal_config(home)

    result = _run(["cron"], tmp_path)

    assert result.returncode != 0
    stderr = result.stderr + result.stdout
    assert ".env" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_cron_missing_operator_credential_stops_before_init_bootstrap(
    tmp_path: Path,
) -> None:
    """A missing Operator Credential stops cron before Init Bootstrap writes setup scripts."""
    home = tmp_path / "application-pipeline"
    _minimal_config(home)

    result = _run(["cron"], tmp_path)

    assert result.returncode != 0
    assert not (home / "setup" / "cron-install.sh").exists()


def test_run_no_judge_requires_operator_credential(tmp_path: Path) -> None:
    """run --no-judge still requires a populated Operator Credential."""
    home = tmp_path / "application-pipeline"
    _minimal_config(home)

    result = _run(["run", "--no-judge"], tmp_path)

    assert result.returncode != 0
    stderr = result.stderr + result.stdout
    assert ".env" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_cron_no_judge_requires_operator_credential(tmp_path: Path) -> None:
    """cron --no-judge still requires a populated Operator Credential."""
    home = tmp_path / "application-pipeline"
    _minimal_config(home)

    result = _run(["cron", "--no-judge"], tmp_path)

    assert result.returncode != 0
    stderr = result.stderr + result.stdout
    assert ".env" in stderr
    assert "OPENCODE_GO_API_KEY" in stderr


def test_init_does_not_require_populated_operator_credential(tmp_path: Path) -> None:
    """application-pipeline init seeds the operator credential placeholder without one present."""
    result = _run(["init"], tmp_path)

    assert result.returncode == 0
    env_path = tmp_path / "application-pipeline" / ".env"
    assert env_path.exists()
    assert "OPENCODE_GO_API_KEY=" in env_path.read_text(encoding="utf-8")


def test_compile_cv_does_not_require_populated_operator_credential(
    tmp_path: Path,
) -> None:
    """compile-cv fails on its own prerequisites, not on a missing Operator Credential."""
    result = _run(["compile-cv", "some-app"], tmp_path)

    assert result.returncode != 0
    stderr = result.stderr + result.stdout
    assert "OPENCODE_GO_API_KEY" not in stderr
    assert ".env" not in stderr
