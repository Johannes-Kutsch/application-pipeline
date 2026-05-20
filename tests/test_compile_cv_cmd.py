"""Tests for the compile-cv subcommand."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _run_main(args: list[str]) -> None:
    from application_pipeline.__main__ import main

    old = sys.argv
    try:
        sys.argv = ["application-pipeline", *args]
        main()
    finally:
        sys.argv = old


def _make_fake_pdflatex(build_dir: Path) -> MagicMock:
    """Return a mock subprocess.run that simulates a successful pdflatex run."""

    def fake_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        cwd = Path(str(kwargs.get("cwd", build_dir)))
        jobname: str | None = None
        for i, arg in enumerate(cmd):
            if arg == "-jobname" and i + 1 < len(cmd):
                jobname = cmd[i + 1]
                break
        if jobname:
            (cwd / f"{jobname}.pdf").write_bytes(b"%PDF-1.4 fake\n")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    return MagicMock(side_effect=fake_run)


@pytest.fixture()
def app_dir(tmp_path: Path) -> Path:
    d = tmp_path / "application"
    d.mkdir()
    (d / "cv.tex").write_text("% application cv\n")
    return d


@pytest.fixture()
def settings_dir(tmp_path: Path) -> Path:
    s = tmp_path / "settings"
    (s / "user-info").mkdir(parents=True)
    return s


def test_compile_cv_produces_three_pdfs(
    app_dir: Path,
    settings_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(settings_dir))
    build_dir = app_dir / ".build"
    mock_run = _make_fake_pdflatex(build_dir)
    monkeypatch.setattr("subprocess.run", mock_run)

    from application_pipeline.compile_cv_cmd import compile_cv

    compile_cv(app_dir)

    assert (app_dir / "cover.pdf").exists()
    assert (app_dir / "resume.pdf").exists()
    assert (app_dir / "combined.pdf").exists()


def test_compile_cv_removes_build_dir_on_success(
    app_dir: Path,
    settings_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(settings_dir))
    build_dir = app_dir / ".build"
    mock_run = _make_fake_pdflatex(build_dir)
    monkeypatch.setattr("subprocess.run", mock_run)

    from application_pipeline.compile_cv_cmd import compile_cv

    compile_cv(app_dir)

    assert not (app_dir / ".build").exists()


def test_compile_cv_overwrites_existing_pdfs(
    app_dir: Path,
    settings_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(settings_dir))
    build_dir = app_dir / ".build"
    (app_dir / "cover.pdf").write_bytes(b"old cover")
    (app_dir / "resume.pdf").write_bytes(b"old resume")
    (app_dir / "combined.pdf").write_bytes(b"old combined")
    mock_run = _make_fake_pdflatex(build_dir)
    monkeypatch.setattr("subprocess.run", mock_run)

    from application_pipeline.compile_cv_cmd import compile_cv

    compile_cv(app_dir)

    assert (app_dir / "cover.pdf").read_bytes() == b"%PDF-1.4 fake\n"
    assert (app_dir / "resume.pdf").read_bytes() == b"%PDF-1.4 fake\n"
    assert (app_dir / "combined.pdf").read_bytes() == b"%PDF-1.4 fake\n"


def test_compile_cv_uses_env_var_settings_dir(
    app_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_settings = tmp_path / "custom-settings"
    user_info = custom_settings / "user-info"
    user_info.mkdir(parents=True)
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(custom_settings))

    captured_cmds: list[list[str]] = []

    def fake_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        captured_cmds.append(cmd)
        cwd = Path(str(kwargs.get("cwd", app_dir / ".build")))
        for i, arg in enumerate(cmd):
            if arg == "-jobname" and i + 1 < len(cmd):
                (cwd / f"{cmd[i + 1]}.pdf").write_bytes(b"%PDF-1.4 fake\n")
                break
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)

    from application_pipeline.compile_cv_cmd import compile_cv

    compile_cv(app_dir)

    assert any(str(user_info) in arg for cmd in captured_cmds for arg in cmd)


def test_compile_cv_exits_nonzero_on_first_failure(
    app_dir: Path,
    settings_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(settings_dir))

    call_count = 0

    def failing_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal call_count
        call_count += 1
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"LaTeX error")

    monkeypatch.setattr("subprocess.run", failing_run)

    from application_pipeline.compile_cv_cmd import compile_cv

    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir)

    assert exc_info.value.code != 0
    assert call_count == 1, "should stop after first failure"


def test_compile_cv_leaves_build_dir_on_failure(
    app_dir: Path,
    settings_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(settings_dir))

    def failing_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", failing_run)

    from application_pipeline.compile_cv_cmd import compile_cv

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    assert (app_dir / ".build").exists()


def test_compile_cv_does_not_write_pdfs_to_dir_on_failure(
    app_dir: Path,
    settings_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(settings_dir))

    def failing_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", failing_run)

    from application_pipeline.compile_cv_cmd import compile_cv

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    assert not (app_dir / "cover.pdf").exists()
    assert not (app_dir / "resume.pdf").exists()
    assert not (app_dir / "combined.pdf").exists()


def test_compile_cv_emits_error_blob_to_stderr_on_failure(
    app_dir: Path,
    settings_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(settings_dir))

    def failing_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        cwd = Path(str(kwargs.get("cwd", app_dir / ".build")))
        for i, arg in enumerate(cmd):
            if arg == "-jobname" and i + 1 < len(cmd):
                log_path = cwd / f"{cmd[i + 1]}.log"
                log_path.write_text(
                    "This is pdflatex\n"
                    "! Undefined control sequence.\n"
                    "l.42 \\badmacro\n"
                    "           {foo}\n"
                    "? \n",
                    encoding="utf-8",
                )
                break
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", failing_run)

    from application_pipeline.compile_cv_cmd import compile_cv

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    err = capsys.readouterr().err
    assert "! Undefined control sequence." in err
    assert "\\badmacro" in err


def test_compile_cv_via_cli_dispatch(
    app_dir: Path,
    settings_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(settings_dir))
    build_dir = app_dir / ".build"
    mock_run = _make_fake_pdflatex(build_dir)
    monkeypatch.setattr("subprocess.run", mock_run)

    _run_main(["compile-cv", str(app_dir)])

    assert (app_dir / "cover.pdf").exists()
    assert (app_dir / "resume.pdf").exists()
    assert (app_dir / "combined.pdf").exists()
