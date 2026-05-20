"""Tests for the compile-cv subcommand."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from application_pipeline.__main__ import main
from application_pipeline.compile_cv_cmd import compile_cv

RunFn = Callable[..., subprocess.CompletedProcess[bytes]]


def _jobname(cmd: list[str]) -> str | None:
    for i, arg in enumerate(cmd):
        if arg == "-jobname" and i + 1 < len(cmd):
            return cmd[i + 1]
    return None


def _write_fake_pdf(cmd: list[str], cwd: Path) -> None:
    jobname = _jobname(cmd)
    if jobname:
        (cwd / f"{jobname}.pdf").write_bytes(b"%PDF-1.4 fake\n")


def _fake_pdflatex_success(
    cmd: list[str], **kwargs: object
) -> subprocess.CompletedProcess[bytes]:
    cwd = Path(str(kwargs["cwd"]))
    _write_fake_pdf(cmd, cwd)
    return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")


def _fake_pdflatex_failure(
    cmd: list[str], **kwargs: object
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"")


@pytest.fixture()
def app_dir(tmp_path: Path) -> Path:
    d = tmp_path / "application"
    d.mkdir()
    (d / "cv.tex").write_text("% application cv\n")
    return d


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    (root / "application-pipeline" / "user-info").mkdir(parents=True)
    (root / "application-pipeline" / "config.py").write_text("")
    monkeypatch.chdir(root)
    return root


@pytest.fixture()
def patch_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[RunFn], None]:
    def patch(fn: RunFn) -> None:
        monkeypatch.setattr("subprocess.run", fn)

    return patch


def test_compile_cv_produces_three_pdfs(
    app_dir: Path,
    project_root: Path,
    patch_subprocess: Callable[[RunFn], None],
) -> None:
    patch_subprocess(_fake_pdflatex_success)

    compile_cv(app_dir)

    assert (app_dir / "cover.pdf").exists()
    assert (app_dir / "resume.pdf").exists()
    assert (app_dir / "combined.pdf").exists()


def test_compile_cv_removes_build_dir_on_success(
    app_dir: Path,
    project_root: Path,
    patch_subprocess: Callable[[RunFn], None],
) -> None:
    patch_subprocess(_fake_pdflatex_success)

    compile_cv(app_dir)

    assert not (app_dir / ".build").exists()


def test_compile_cv_overwrites_existing_pdfs(
    app_dir: Path,
    project_root: Path,
    patch_subprocess: Callable[[RunFn], None],
) -> None:
    for name in ("cover", "resume", "combined"):
        (app_dir / f"{name}.pdf").write_bytes(b"stale")
    patch_subprocess(_fake_pdflatex_success)

    compile_cv(app_dir)

    for name in ("cover", "resume", "combined"):
        assert (app_dir / f"{name}.pdf").read_bytes() == b"%PDF-1.4 fake\n"


def test_compile_cv_ignores_application_pipeline_home(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    irrelevant = project_root / "irrelevant"
    (irrelevant / "user-info").mkdir(parents=True)
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(irrelevant))

    captured_cmds: list[list[str]] = []

    def capturing_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        captured_cmds.append(cmd)
        return _fake_pdflatex_success(cmd, **kwargs)

    monkeypatch.setattr("subprocess.run", capturing_run)

    compile_cv(app_dir)

    expected_user_info = str(
        (project_root / "application-pipeline" / "user-info").resolve()
    )
    assert any(expected_user_info in arg for cmd in captured_cmds for arg in cmd)
    assert not any(str(irrelevant) in arg for cmd in captured_cmds for arg in cmd)


def test_compile_cv_exits_nonzero_on_first_failure(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    def failing_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal call_count
        call_count += 1
        return _fake_pdflatex_failure(cmd, **kwargs)

    monkeypatch.setattr("subprocess.run", failing_run)

    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir)

    assert exc_info.value.code != 0
    assert call_count == 1, "should stop after first failure"


def test_compile_cv_leaves_build_dir_on_failure(
    app_dir: Path,
    project_root: Path,
    patch_subprocess: Callable[[RunFn], None],
) -> None:
    patch_subprocess(_fake_pdflatex_failure)

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    assert (app_dir / ".build").exists()


def test_compile_cv_does_not_write_pdfs_to_dir_on_failure(
    app_dir: Path,
    project_root: Path,
    patch_subprocess: Callable[[RunFn], None],
) -> None:
    patch_subprocess(_fake_pdflatex_failure)

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    for name in ("cover", "resume", "combined"):
        assert not (app_dir / f"{name}.pdf").exists()


def test_compile_cv_emits_error_blob_to_stderr_on_failure(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failing_run_with_log(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        cwd = Path(str(kwargs["cwd"]))
        jobname = _jobname(cmd)
        if jobname:
            (cwd / f"{jobname}.log").write_text(
                "This is pdflatex\n"
                "! Undefined control sequence.\n"
                "l.42 \\badmacro\n"
                "           {foo}\n"
                "? \n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", failing_run_with_log)

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    err = capsys.readouterr().err
    assert "! Undefined control sequence." in err
    assert "\\badmacro" in err


def test_compile_cv_via_cli_dispatch(
    app_dir: Path,
    project_root: Path,
    patch_subprocess: Callable[[RunFn], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_subprocess(_fake_pdflatex_success)
    monkeypatch.setattr(
        sys, "argv", ["application-pipeline", "compile-cv", str(app_dir)]
    )

    main()

    assert (app_dir / "cover.pdf").exists()
    assert (app_dir / "resume.pdf").exists()
    assert (app_dir / "combined.pdf").exists()


def test_compile_cv_uses_cwd_relative_user_info(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_cmds: list[list[str]] = []

    def capturing_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        captured_cmds.append(cmd)
        return _fake_pdflatex_success(cmd, **kwargs)

    monkeypatch.setattr("subprocess.run", capturing_run)

    compile_cv(app_dir)

    expected_user_info = str(
        (project_root / "application-pipeline" / "user-info").resolve()
    )
    assert any(expected_user_info in arg for cmd in captured_cmds for arg in cmd)


def test_compile_cv_missing_config_exits_2_without_build_dir(
    app_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    monkeypatch.chdir(empty_root)

    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir)

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "no application-pipeline/config.py in" in err
    assert "did you forget to cd, or run init?" in err
    assert not (app_dir / ".build").exists()
