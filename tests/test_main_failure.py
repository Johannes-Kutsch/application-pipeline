"""Tests that __main__ writes failure reports to <cwd>/application-pipeline/failures/."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from application_pipeline.failure_report import write_failure

_PYTHONPATH = os.pathsep.join(p for p in sys.path if p)

_MALFORMED_CONFIG = """\
KEYWORDS = ["python"]
SKILLS = ["python"]
LOCATIONS = ["Berlin"]
# SOURCES is intentionally missing to trigger ConfigError
"""


def _run_main(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "application_pipeline", "run"],
        cwd=str(cwd),
        env={**os.environ, "PYTHONPATH": _PYTHONPATH},
        capture_output=True,
        text=True,
    )


def test_write_failure_writes_directly_into_given_failures_dir(tmp_path: Path) -> None:
    """write_failure should accept the failures directory directly and write there,
    not into a 'failures' subdirectory of it."""
    failures_dir = tmp_path / "data" / "failures"

    path = write_failure("stage", ValueError("boom"), "log tail", failures_dir)

    assert path.parent == failures_dir, (
        f"Expected report directly in {failures_dir}, got {path.parent}"
    )
    assert path.exists()


def test_run_inside_data_dir_hints_cd_dotdot(tmp_path: Path) -> None:
    data_dir = tmp_path / "application-pipeline"
    data_dir.mkdir()
    (data_dir / "config.py").write_text("")
    result = _run_main(data_dir)
    assert result.returncode == 2
    assert "inside the data directory" in result.stderr
    assert "cd .." in result.stderr


def test_run_no_config_anywhere_shows_original_message(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run_main(empty)
    assert result.returncode == 2
    assert "no application-pipeline/config.py in" in result.stderr
    assert "did you forget to cd, or run init?" in result.stderr


def test_startup_failure_writes_to_home_failures_dir(tmp_path: Path) -> None:
    home = tmp_path / "application-pipeline"
    home.mkdir()
    (home / "config.py").write_text(_MALFORMED_CONFIG)
    (home / ".env").write_text("OPENCODE_GO_API_KEY=test-key\n", encoding="utf-8")

    _run_main(tmp_path)

    assert not (tmp_path / "results").exists(), (
        "Should not create results/ directly under cwd"
    )
    assert not (tmp_path / "failures").exists(), (
        "Should not create failures/ directly under cwd"
    )
    failures_dir = home / ".runtime-data" / "failures"
    assert failures_dir.is_dir()
    assert len(list(failures_dir.glob("*.md"))) == 1
