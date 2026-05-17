"""Tests that __main__ writes failure reports to <data_dir>/failures/, not under CWD."""

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


def _run_main(config_path: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "application_pipeline", config_path],
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


def test_startup_failure_does_not_write_under_cwd(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "config.py").write_text(_MALFORMED_CONFIG)

    cwd = tmp_path / "other"
    cwd.mkdir()

    _run_main(str(data_dir / "config.py"), cwd)

    assert not (cwd / "results").exists(), "Should not create results/ under CWD"
    assert not (cwd / "failures").exists(), "Should not create failures/ under CWD"
    failures_dir = data_dir / "failures"
    assert failures_dir.is_dir()
    assert len(list(failures_dir.glob("*.md"))) == 1
