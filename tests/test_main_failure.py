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


def test_startup_failure_writes_to_home_failures_dir(tmp_path: Path) -> None:
    home = tmp_path / "application-pipeline"
    home.mkdir()
    (home / "config.py").write_text(_MALFORMED_CONFIG)

    _run_main(tmp_path)

    assert not (tmp_path / "results").exists(), (
        "Should not create results/ directly under cwd"
    )
    assert not (tmp_path / "failures").exists(), (
        "Should not create failures/ directly under cwd"
    )
    failures_dir = home / "failures"
    assert failures_dir.is_dir()
    assert len(list(failures_dir.glob("*.md"))) == 1
