"""Tests for cron.sh and cron-install.sh shell script templates (issue #624)."""

from __future__ import annotations

import importlib.resources
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest


def _setup_template(name: str) -> str:
    node = (
        importlib.resources.files("application_pipeline.templates")
        / "application-pipeline"
        / "setup"
        / name
    )
    return node.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cron_sh_text() -> str:
    return _setup_template("cron.sh")


@pytest.fixture(scope="module")
def cron_install_sh_text() -> str:
    return _setup_template("cron-install.sh")


# ---------------------------------------------------------------------------
# cron.sh structural checks
# ---------------------------------------------------------------------------


def test_cron_sh_uses_venv_prefixed_pip(cron_sh_text: str) -> None:
    """No bare `pip` command — must use .venv/bin/pip."""
    import re

    assert ".venv/bin/pip" in cron_sh_text
    # Look for lines where pip is a command (at start of line or in $(...) subshell),
    # not inside echo strings or heredoc content.
    bare_cmd_lines = [
        line
        for line in cron_sh_text.splitlines()
        if re.search(r"(?:^\s*|(?<=\$\())pip\s", line) and "/bin/pip" not in line
    ]
    assert bare_cmd_lines == [], f"Lines with bare pip command: {bare_cmd_lines}"


def test_cron_sh_uses_venv_prefixed_application_pipeline(cron_sh_text: str) -> None:
    """No bare `application-pipeline` command — must use .venv/bin/application-pipeline."""
    import re

    assert ".venv/bin/application-pipeline" in cron_sh_text
    # Look for lines where application-pipeline is a command (at start of line
    # or in a $(...) subshell), not inside echo strings.
    bare_cmd_lines = [
        line
        for line in cron_sh_text.splitlines()
        if re.search(r"(?:^\s*|(?<=\$\())application-pipeline\s", line)
        and "/bin/application-pipeline" not in line
    ]
    assert bare_cmd_lines == [], (
        f"Lines with bare application-pipeline command: {bare_cmd_lines}"
    )


def test_cron_sh_has_no_flock(cron_sh_text: str) -> None:
    """cron.sh must not contain any flock call or .cron.lock reference."""
    assert "flock" not in cron_sh_text
    assert ".cron.lock" not in cron_sh_text


# ---------------------------------------------------------------------------
# cron.sh behavioral checks (subprocess)
# ---------------------------------------------------------------------------


def _make_script_env(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """
    Set up a fake working directory so cron.sh can run in isolation.

    Layout (mirrors real install):
      <tmp>/                          ← cwd; cd "$(dirname "$0")/../.." lands here
        application-pipeline/         ← settings-dir
          setup/
            cron.sh                   ← script under test
          .runtime-data/logs/
          .runtime-data/failures/
        .venv/bin/
          pip                         ← stub
          application-pipeline        ← stub
    """
    ap_dir = tmp_path / "application-pipeline"
    setup_dir = ap_dir / "setup"
    setup_dir.mkdir(parents=True)
    (ap_dir / ".runtime-data" / "logs").mkdir(parents=True)
    (ap_dir / ".runtime-data" / "failures").mkdir(parents=True)

    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)

    cron_sh = setup_dir / "cron.sh"
    cron_sh.write_text(_setup_template("cron.sh"), encoding="utf-8")
    cron_sh.chmod(cron_sh.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = str(venv_bin) + ":" + env.get("PATH", "")
    return cron_sh, venv_bin, env


def _write_stub(venv_bin: Path, name: str, body: str, exit_code: int = 0) -> Path:
    stub = venv_bin / name
    stub.write_text(
        f"#!/usr/bin/env bash\n{body}\nexit {exit_code}\n", encoding="utf-8"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def test_cron_sh_exits_with_error_when_venv_missing(tmp_path: Path) -> None:
    """When .venv/ does not exist, cron.sh exits non-zero with a message on stderr."""
    setup_dir = tmp_path / "application-pipeline" / "setup"
    setup_dir.mkdir(parents=True)
    cron_sh = setup_dir / "cron.sh"
    cron_sh.write_text(_setup_template("cron.sh"), encoding="utf-8")
    cron_sh.chmod(cron_sh.stat().st_mode | stat.S_IEXEC)

    result = subprocess.run(
        ["bash", str(cron_sh)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    combined = result.stderr + result.stdout
    assert ".venv" in combined
    assert "pip install" in combined


def test_cron_sh_pip_failure_warns_but_continues(tmp_path: Path) -> None:
    """A pip upgrade failure prints a warning but init --refresh and run still execute."""
    cron_sh, venv_bin, env = _make_script_env(tmp_path)

    # pip always fails
    _write_stub(venv_bin, "pip", 'echo "pip error" >&2', exit_code=1)
    # application-pipeline always succeeds
    _write_stub(venv_bin, "application-pipeline", "", exit_code=0)

    result = subprocess.run(
        ["bash", str(cron_sh)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    # Should not abort — exit 0
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    combined = result.stderr + result.stdout
    assert "WARNING" in combined or "warning" in combined.lower()


def test_cron_sh_init_failure_writes_failure_report(tmp_path: Path) -> None:
    """When init --refresh exits non-zero, a Failure Report appears in .runtime-data/failures/."""
    cron_sh, venv_bin, env = _make_script_env(tmp_path)

    _write_stub(venv_bin, "pip", "", exit_code=0)
    # application-pipeline: fail on init, succeed on run
    (venv_bin / "application-pipeline").write_text(
        textwrap.dedent("""\
            #!/usr/bin/env bash
            if [[ "$*" == *"init"* ]]; then
                echo "init failed" >&2
                exit 1
            fi
            exit 0
        """),
        encoding="utf-8",
    )
    (venv_bin / "application-pipeline").chmod(
        (venv_bin / "application-pipeline").stat().st_mode | stat.S_IEXEC
    )

    subprocess.run(
        ["bash", str(cron_sh)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    failures_dir = tmp_path / "application-pipeline" / ".runtime-data" / "failures"
    failure_files = list(failures_dir.glob("*.md"))
    assert len(failure_files) >= 1, "Expected a Failure Report markdown file"


def test_cron_sh_run_failure_writes_failure_report(tmp_path: Path) -> None:
    """When run exits non-zero, a Failure Report appears in .runtime-data/failures/."""
    cron_sh, venv_bin, env = _make_script_env(tmp_path)

    _write_stub(venv_bin, "pip", "", exit_code=0)
    (venv_bin / "application-pipeline").write_text(
        textwrap.dedent("""\
            #!/usr/bin/env bash
            if [[ "$*" == *"run"* ]]; then
                echo "run failed" >&2
                exit 1
            fi
            exit 0
        """),
        encoding="utf-8",
    )
    (venv_bin / "application-pipeline").chmod(
        (venv_bin / "application-pipeline").stat().st_mode | stat.S_IEXEC
    )

    subprocess.run(
        ["bash", str(cron_sh)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    failures_dir = tmp_path / "application-pipeline" / ".runtime-data" / "failures"
    failure_files = list(failures_dir.glob("*.md"))
    assert len(failure_files) >= 1, "Expected a Failure Report markdown file"


def test_cron_sh_log_trimmed_to_10000_lines(tmp_path: Path) -> None:
    """After a successful tick, cron.log is trimmed to at most 10,000 lines."""
    cron_sh, venv_bin, env = _make_script_env(tmp_path)

    _write_stub(venv_bin, "pip", "", exit_code=0)
    _write_stub(venv_bin, "application-pipeline", "", exit_code=0)

    # Seed cron.log with 15000 lines
    log_path = tmp_path / "application-pipeline" / ".runtime-data" / "logs" / "cron.log"
    log_path.write_text("line\n" * 15000, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(cron_sh)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}"
    line_count = len(log_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= 10000, f"Expected ≤10000 lines, got {line_count}"


def test_cron_sh_no_judge_passes_flag_to_run(tmp_path: Path) -> None:
    """./cron.sh --no-judge passes --no-judge to application-pipeline run."""
    cron_sh, venv_bin, env = _make_script_env(tmp_path)

    _write_stub(venv_bin, "pip", "", exit_code=0)

    # Record args passed to application-pipeline
    (venv_bin / "application-pipeline").write_text(
        textwrap.dedent("""\
            #!/usr/bin/env bash
            echo "$*" >> /tmp/ap_calls_$$.txt
            exit 0
        """),
        encoding="utf-8",
    )
    (venv_bin / "application-pipeline").chmod(
        (venv_bin / "application-pipeline").stat().st_mode | stat.S_IEXEC
    )

    # Use a unique temp file per test
    calls_file = tmp_path / "ap_calls.txt"

    (venv_bin / "application-pipeline").write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            echo "$*" >> {calls_file}
            exit 0
        """),
        encoding="utf-8",
    )
    (venv_bin / "application-pipeline").chmod(
        (venv_bin / "application-pipeline").stat().st_mode | stat.S_IEXEC
    )

    result = subprocess.run(
        ["bash", str(cron_sh), "--no-judge"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    calls = calls_file.read_text(encoding="utf-8").splitlines()
    run_calls = [c for c in calls if c.startswith("run")]
    assert any("--no-judge" in c for c in run_calls), (
        f"Expected 'run --no-judge' in application-pipeline calls, got: {calls}"
    )


def test_cron_sh_without_no_judge_does_not_pass_flag(tmp_path: Path) -> None:
    """./cron.sh without --no-judge invokes application-pipeline run without --no-judge."""
    cron_sh, venv_bin, env = _make_script_env(tmp_path)

    _write_stub(venv_bin, "pip", "", exit_code=0)

    calls_file = tmp_path / "ap_calls.txt"
    (venv_bin / "application-pipeline").write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            echo "$*" >> {calls_file}
            exit 0
        """),
        encoding="utf-8",
    )
    (venv_bin / "application-pipeline").chmod(
        (venv_bin / "application-pipeline").stat().st_mode | stat.S_IEXEC
    )

    result = subprocess.run(
        ["bash", str(cron_sh)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    calls = calls_file.read_text(encoding="utf-8").splitlines()
    run_calls = [c for c in calls if c.startswith("run")]
    assert run_calls, "application-pipeline run must be called"
    assert not any("--no-judge" in c for c in run_calls), (
        f"--no-judge must not appear in run call without the flag: {run_calls}"
    )


# ---------------------------------------------------------------------------
# cron-install.sh structural checks
# ---------------------------------------------------------------------------


def test_cron_install_sh_log_path_uses_runtime_data(cron_install_sh_text: str) -> None:
    """CRON_LINE must redirect to .runtime-data/logs/cron.log, not logs/cron.log."""
    assert ".runtime-data/logs/cron.log" in cron_install_sh_text
    # Must NOT use the old logs/ path (without .runtime-data)
    import re

    old_path = re.search(r"\$SETTINGS_DIR/logs/cron\.log", cron_install_sh_text)
    assert old_path is None, (
        "cron-install.sh still uses old $SETTINGS_DIR/logs/cron.log path"
    )


def test_cron_install_sh_creates_runtime_data_logs_dir(
    cron_install_sh_text: str,
) -> None:
    """cron-install.sh must mkdir -p .runtime-data/logs/ before writing the crontab."""
    assert ".runtime-data/logs" in cron_install_sh_text
    assert "mkdir -p" in cron_install_sh_text


def test_cron_install_sh_schedule_is_weekdays_0030(cron_install_sh_text: str) -> None:
    """Cron schedule must be '30 0 * * 1-5' (weekdays at 00:30)."""
    assert "30 0 * * 1-5" in cron_install_sh_text
