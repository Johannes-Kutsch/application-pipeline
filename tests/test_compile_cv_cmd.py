"""Tests for the compile-cv subcommand."""

from __future__ import annotations

import re
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
    (d / "cv.tex").write_text(_valid_cv_tex(), encoding="utf-8")
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

    expected_user_info = (
        project_root / "application-pipeline" / "user-info"
    ).resolve().as_posix()
    assert any(expected_user_info in arg for cmd in captured_cmds for arg in cmd)
    assert not any(
        irrelevant.resolve().as_posix() in arg
        for cmd in captured_cmds
        for arg in cmd
    )


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

    expected_user_info = (
        project_root / "application-pipeline" / "user-info"
    ).resolve().as_posix()
    assert any(expected_user_info in arg for cmd in captured_cmds for arg in cmd)


def _valid_cv_tex() -> str:
    slots = [
        ("recipient_company", "Firma GmbH"),
        ("recipient_name", "Frau Dr. Müller"),
        ("recipient_street", "Musterstraße 1"),
        ("recipient_zip_city", "12345 Berlin"),
        ("opening", "Sehr geehrte Damen und Herren,"),
        ("cover_intro", "Ich bewerbe mich hiermit."),
        ("cover_pivot", "Mein Hintergrund ist relevant."),
        ("cover_fit", "Ich passe gut zu Ihrer Firma."),
        ("cover_closing", "Ich freue mich auf Ihre Antwort."),
        (
            "resume_berufserfahrung",
            r"\cventry{2020--2023}{Developer}{Firma}{Berlin}{}{}",
        ),
        ("resume_ausbildung", r"\cventry{2016--2020}{B.Sc.}{TU Berlin}{Berlin}{}{}"),
        ("resume_projekte", r"\cventry{2021}{Projekt}{}{}{}{Beschreibung}"),
        ("skills_block", "Python, LaTeX"),
    ]
    return "".join(f"%% SLOT: {name}\n{body}\n" for name, body in slots)


def test_compile_cv_missing_cv_tex_exits_with_write_cv_message(
    project_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_dir_no_cv = tmp_path / "app_no_cv"
    app_dir_no_cv.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir_no_cv)

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "/write-cv" in err


def test_compile_cv_malformed_cv_tex_exits_naming_missing_slot(
    project_root: Path,
    app_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (app_dir / "cv.tex").write_text(
        "%% SLOT: recipient_company\nFirma GmbH\n", encoding="utf-8"
    )

    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir)

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "resume_ausbildung" in err


def test_compile_cv_build_cv_tex_has_substituted_content(
    app_dir: Path,
    project_root: Path,
    patch_subprocess: Callable[[RunFn], None],
) -> None:
    patch_subprocess(_fake_pdflatex_failure)

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    build_cv = app_dir / ".build" / "cv.tex"
    assert build_cv.exists()
    content = build_cv.read_text(encoding="utf-8")
    assert "<<" not in content
    assert "Firma GmbH" in content


def test_compile_cv_cv_data_dir_uses_forward_slashes(
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

    cv_data_args = [arg for cmd in captured_cmds for arg in cmd if "CvDataDir" in arg]
    assert cv_data_args, "no CvDataDir arg found in pdflatex commands"
    for arg in cv_data_args:
        m = re.search(r"\\def\\CvDataDir\{([^}]+)\}", arg)
        assert m is not None, f"could not parse CvDataDir from: {arg}"
        assert "\\" not in m.group(1), "path must use forward slashes"


def test_compile_cv_three_resume_slots_independently_substituted(
    project_root: Path,
    tmp_path: Path,
    patch_subprocess: Callable[[RunFn], None],
) -> None:
    app_dir = tmp_path / "app_resume"
    app_dir.mkdir()
    slots = [
        ("recipient_company", "Firma GmbH"),
        ("recipient_name", "Frau Müller"),
        ("recipient_street", "Musterstraße 1"),
        ("recipient_zip_city", "12345 Berlin"),
        ("opening", "Sehr geehrte Damen und Herren,"),
        ("cover_intro", "Intro."),
        ("cover_pivot", "Pivot."),
        ("cover_fit", "Fit."),
        ("cover_closing", "Closing."),
        ("resume_berufserfahrung", "BERUFSINHALT"),
        ("resume_ausbildung", "AUSBILDUNGSINHALT"),
        ("resume_projekte", "PROJEKTINHALT"),
        ("skills_block", "KENNTNISSE"),
    ]
    (app_dir / "cv.tex").write_text(
        "".join(f"%% SLOT: {n}\n{b}\n" for n, b in slots), encoding="utf-8"
    )
    patch_subprocess(_fake_pdflatex_failure)

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    content = (app_dir / ".build" / "cv.tex").read_text(encoding="utf-8")
    assert "BERUFSINHALT" in content
    assert "AUSBILDUNGSINHALT" in content
    assert "PROJEKTINHALT" in content
    assert "<<RESUME_BERUFSERFAHRUNG>>" not in content
    assert "<<RESUME_AUSBILDUNG>>" not in content
    assert "<<RESUME_PROJEKTE>>" not in content


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
