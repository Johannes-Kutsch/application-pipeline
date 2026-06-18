"""Tests for the compile-cv subcommand."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import application_pipeline.compile_cv_cmd as compile_cv_cmd_module
from application_pipeline.__main__ import main
from application_pipeline.compile_cv_cmd import compile_cv
from application_pipeline.cv_slot_contract import SLOT_NAMES
from application_pipeline.latex import slot_map


def _write_fake_pdf(build_dir: Path, build_name: str) -> None:
    slots = slot_map.parse(build_dir.parent / "cv.tex")
    if build_name == "cover":
        rendered_cv = "\n".join(
            [
                slots["opening"],
                slots["cover_intro"],
                slots["cover_pivot"],
                slots["cover_fit"],
                slots["cover_closing"],
            ]
        )
    elif build_name == "resume":
        rendered_cv = "\n".join(
            [
                slots["resume_berufserfahrung"],
                slots["resume_ausbildung"],
                slots["resume_projekte"],
                slots["skills_block"],
            ]
        )
    elif build_name == "combined":
        rendered_cv = "\n".join(
            [
                slots["opening"],
                slots["cover_intro"],
                slots["cover_pivot"],
                slots["cover_fit"],
                slots["cover_closing"],
                slots["resume_berufserfahrung"],
                slots["resume_ausbildung"],
                slots["resume_projekte"],
                slots["skills_block"],
            ]
        )
    else:
        raise AssertionError(f"unexpected build name: {build_name}")
    (build_dir / f"{build_name}.pdf").write_bytes(
        b"%PDF-1.4 fake\n" + rendered_cv.encode("utf-8")
    )


@dataclass(frozen=True, slots=True)
class _PdflatexOutcome:
    returncode: int
    log_text: str | None = None


@dataclass(frozen=True, slots=True)
class _CapturedRun:
    cmd: list[str]
    cwd: Path
    capture_output: bool
    env: dict[str, str]


def _build_name_from_cmd(cmd: list[str]) -> str:
    return cmd[cmd.index("-jobname") + 1]


def _install_fake_pdflatex(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[_PdflatexOutcome],
    *,
    captured_runs: list[_CapturedRun] | None = None,
) -> None:
    queue = list(outcomes)

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path,
        capture_output: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        if captured_runs is not None:
            captured_runs.append(
                _CapturedRun(
                    cmd=cmd,
                    cwd=cwd,
                    capture_output=capture_output,
                    env=dict(env),
                )
            )
        if not queue:
            raise AssertionError("unexpected pdflatex pass")
        outcome = queue.pop(0)
        build_name = _build_name_from_cmd(cmd)
        if outcome.returncode == 0:
            _write_fake_pdf(cwd, build_name)
        elif outcome.log_text is not None:
            (cwd / f"{build_name}.log").write_text(
                outcome.log_text,
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(
            cmd, outcome.returncode, stdout=b"", stderr=b""
        )

    monkeypatch.setattr(
        "application_pipeline.compile_cv_local.subprocess.run", fake_run
    )


def _install_passing_pdflatex(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_pdflatex(
        monkeypatch,
        [_PdflatexOutcome(returncode=0) for _ in range(6)],
    )


def _install_failing_pdflatex(
    monkeypatch: pytest.MonkeyPatch,
    *,
    log_text: str | None = None,
) -> None:
    _install_fake_pdflatex(
        monkeypatch,
        [_PdflatexOutcome(returncode=1, log_text=log_text)],
    )


def _published_pdf(app_dir: Path, build_name: str) -> Path:
    return app_dir / f"{build_name}_{app_dir.name}.pdf"


def _slot_bodies(overrides: dict[str, str] | None = None) -> dict[str, str]:
    bodies = dict(
        zip(
            SLOT_NAMES,
            (
                "Firma GmbH",
                "Frau Dr. Müller",
                "Musterstraße 1",
                "12345 Berlin",
                "Sehr geehrte Damen und Herren,",
                "Ich bewerbe mich hiermit.",
                "Mein Hintergrund ist relevant.",
                "Ich passe gut zu Ihrer Firma.",
                "Ich freue mich auf Ihre Antwort.",
                r"\cventry{2020--2023}{Developer}{Firma}{Berlin}{}{}",
                r"\cventry{2016--2020}{B.Sc.}{TU Berlin}{Berlin}{}{}",
                r"\cventry{2021}{Projekt}{}{}{}{Beschreibung}",
                "Python, LaTeX",
            ),
            strict=True,
        )
    )
    if overrides is not None:
        bodies.update(overrides)
    return {name: bodies[name] for name in SLOT_NAMES}


def _render_cv_tex(bodies: dict[str, str]) -> str:
    return "".join(f"%% SLOT: {name}\n{body}\n" for name, body in bodies.items())


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


def test_compile_cv_produces_three_pdfs(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_passing_pdflatex(monkeypatch)

    compile_cv(app_dir)

    assert (app_dir / "cover_application.pdf").exists()
    assert (app_dir / "resume_application.pdf").exists()
    assert (app_dir / "combined_application.pdf").exists()
    assert not (app_dir / "cover.pdf").exists()
    assert not (app_dir / "resume.pdf").exists()
    assert not (app_dir / "combined.pdf").exists()


def test_compile_cv_supported_build_modes_include_slot_content(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_passing_pdflatex(monkeypatch)

    compile_cv(app_dir)

    assert b"Ich bewerbe mich hiermit." in _published_pdf(app_dir, "cover").read_bytes()
    assert b"Developer" in _published_pdf(app_dir, "resume").read_bytes()
    assert b"Python, LaTeX" in _published_pdf(app_dir, "combined").read_bytes()


def test_compile_cv_publishes_distinct_cover_resume_and_combined_outputs(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_passing_pdflatex(monkeypatch)

    compile_cv(app_dir)

    cover_pdf = _published_pdf(app_dir, "cover").read_bytes()
    resume_pdf = _published_pdf(app_dir, "resume").read_bytes()
    combined_pdf = _published_pdf(app_dir, "combined").read_bytes()

    assert b"Ich bewerbe mich hiermit." in cover_pdf
    assert b"Developer" not in cover_pdf

    assert b"Developer" in resume_pdf
    assert b"Ich bewerbe mich hiermit." not in resume_pdf

    assert b"Ich bewerbe mich hiermit." in combined_pdf
    assert b"Developer" in combined_pdf


def test_compile_cv_removes_build_dir_on_success(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_passing_pdflatex(monkeypatch)

    compile_cv(app_dir)

    assert not (app_dir / ".build").exists()


def test_compile_cv_overwrites_existing_pdfs(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("cover", "resume", "combined"):
        _published_pdf(app_dir, name).write_bytes(b"stale")
        (app_dir / f"{name}.pdf").write_bytes(b"stale-generic")
    _install_passing_pdflatex(monkeypatch)

    compile_cv(app_dir)

    for name in ("cover", "resume", "combined"):
        pdf_bytes = _published_pdf(app_dir, name).read_bytes()
        assert pdf_bytes != b"stale"
        assert pdf_bytes.startswith(b"%PDF-1.4 fake\n")
        assert not (app_dir / f"{name}.pdf").exists()


def test_compile_cv_ignores_application_pipeline_home(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    irrelevant = project_root / "irrelevant"
    (irrelevant / "user-info").mkdir(parents=True)
    monkeypatch.setenv("APPLICATION_PIPELINE_HOME", str(irrelevant))
    monkeypatch.setenv("KEEP_ME", "value")
    monkeypatch.setenv("TEXINPUTS", "host-tex")
    captured_runs: list[_CapturedRun] = []
    _install_fake_pdflatex(
        monkeypatch,
        [_PdflatexOutcome(returncode=0) for _ in range(6)],
        captured_runs=captured_runs,
    )

    compile_cv(app_dir)

    expected_cv_data_dir = (
        (project_root / "application-pipeline" / "user-info" / "cv")
        .resolve()
        .as_posix()
    )
    assert captured_runs
    for run in captured_runs:
        tex_input = run.cmd[-1]
        assert expected_cv_data_dir in tex_input
        assert irrelevant.resolve().as_posix() not in tex_input
        assert run.cwd == app_dir / ".build"
        assert run.capture_output is True
        assert run.env == {**os.environ, "TEXINPUTS": f".{os.pathsep}"}
    assert os.environ["TEXINPUTS"] == "host-tex"


def test_compile_cv_leaves_build_dir_on_failure(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_failing_pdflatex(monkeypatch)

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    assert (app_dir / ".build").exists()


def test_compile_cv_does_not_write_pdfs_to_dir_on_failure(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_failing_pdflatex(monkeypatch)

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    for name in ("cover", "resume", "combined"):
        assert not (app_dir / f"{name}.pdf").exists()
        assert not _published_pdf(app_dir, name).exists()


def test_compile_cv_emits_error_blob_to_stderr_on_failure(
    app_dir: Path,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_failing_pdflatex(
        monkeypatch,
        log_text=(
            "This is pdflatex\n"
            "! Undefined control sequence.\n"
            "l.42 \\badmacro\n"
            "           {foo}\n"
            "? \n"
        ),
    )

    with pytest.raises(SystemExit):
        compile_cv(app_dir)

    err = capsys.readouterr().err
    assert "! Undefined control sequence." in err
    assert "\\badmacro" in err


def test_compile_cv_missing_config_exits_2_before_build_or_pdflatex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root = tmp_path / "project"
    app_dir = tmp_path / "application"
    data_dir = project_root / "application-pipeline"
    data_dir.mkdir(parents=True)
    (data_dir / "config.py").write_text("", encoding="utf-8")
    app_dir.mkdir()
    (app_dir / "cv.tex").write_text(_valid_cv_tex(), encoding="utf-8")
    monkeypatch.chdir(data_dir)
    monkeypatch.setattr(
        "application_pipeline.compile_cv_local.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("pdflatex should not run")
        ),
    )
    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir)

    assert exc_info.value.code == 2
    assert "inside the data directory" in capsys.readouterr().err
    assert not (app_dir / ".build").exists()


def test_compile_cv_missing_cv_tex_exits_before_build_or_pdflatex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root = tmp_path / "project"
    app_dir = tmp_path / "application"
    (project_root / "application-pipeline").mkdir(parents=True)
    (project_root / "application-pipeline" / "config.py").write_text(
        "",
        encoding="utf-8",
    )
    app_dir.mkdir()
    monkeypatch.chdir(project_root)
    monkeypatch.setattr(
        "application_pipeline.compile_cv_local.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("pdflatex should not run")
        ),
    )
    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir)

    assert exc_info.value.code != 0
    assert "did you forget to run /write-cv?" in capsys.readouterr().err
    assert not (app_dir / ".build").exists()


def test_compile_cv_malformed_cv_slot_map_missing_slot_exits_before_build_or_pdflatex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root = tmp_path / "project"
    app_dir = tmp_path / "application"
    (project_root / "application-pipeline").mkdir(parents=True)
    (project_root / "application-pipeline" / "config.py").write_text(
        "",
        encoding="utf-8",
    )
    app_dir.mkdir()
    app_dir.joinpath("cv.tex").write_text(
        _render_cv_tex(
            {
                name: body
                for name, body in _slot_bodies(
                    {
                        "recipient_name": "Frau Dr. Muller",
                        "recipient_street": "Musterstrasse 1",
                    }
                ).items()
                if name != "resume_projekte"
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)
    monkeypatch.setattr(
        "application_pipeline.compile_cv_local.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("pdflatex should not run")
        ),
    )
    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir)

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "missing slots: resume_projekte" in err
    assert not (app_dir / ".build").exists()


def test_compile_cv_via_cli_dispatch(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_app_dirs: list[Path] = []

    def fake_compile_cv(cli_app_dir: Path) -> None:
        captured_app_dirs.append(cli_app_dir)

    monkeypatch.setattr(compile_cv_cmd_module, "compile_cv", fake_compile_cv)
    monkeypatch.setattr(
        sys, "argv", ["application-pipeline", "compile-cv", str(app_dir)]
    )

    main()

    assert captured_app_dirs == [app_dir]


def test_compile_cv_uses_cwd_relative_user_info(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_runs: list[_CapturedRun] = []
    _install_fake_pdflatex(
        monkeypatch,
        [_PdflatexOutcome(returncode=0) for _ in range(6)],
        captured_runs=captured_runs,
    )

    compile_cv(app_dir)

    expected_cv_data_dir = (
        (project_root / "application-pipeline" / "user-info" / "cv")
        .resolve()
        .as_posix()
    )
    assert captured_runs
    assert all(expected_cv_data_dir in run.cmd[-1] for run in captured_runs)


def _valid_cv_tex() -> str:
    return _render_cv_tex(_slot_bodies())


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


def test_compile_cv_cv_data_dir_uses_forward_slashes(
    app_dir: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_runs: list[_CapturedRun] = []
    _install_fake_pdflatex(
        monkeypatch,
        [_PdflatexOutcome(returncode=0) for _ in range(6)],
        captured_runs=captured_runs,
    )

    compile_cv(app_dir)

    assert captured_runs, "no pdflatex calls captured"
    for run in captured_runs:
        tex_input = run.cmd[-1]
        assert "\\" not in tex_input.partition(r"\def\CvDataDir{")[2].partition("}")[0]


def test_compile_cv_three_resume_slots_independently_substituted(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_dir = tmp_path / "app_resume"
    app_dir.mkdir()
    (app_dir / "cv.tex").write_text(
        _render_cv_tex(
            _slot_bodies(
                {
                    "recipient_name": "Frau Müller",
                    "cover_intro": "Intro.",
                    "cover_pivot": "Pivot.",
                    "cover_fit": "Fit.",
                    "cover_closing": "Closing.",
                    "resume_berufserfahrung": "BERUFSINHALT",
                    "resume_ausbildung": "AUSBILDUNGSINHALT",
                    "resume_projekte": "PROJEKTINHALT",
                    "skills_block": "KENNTNISSE",
                }
            )
        ),
        encoding="utf-8",
    )
    _install_passing_pdflatex(monkeypatch)

    compile_cv(app_dir)

    resume_pdf = _published_pdf(app_dir, "resume").read_bytes()
    assert b"BERUFSINHALT" in resume_pdf
    assert b"AUSBILDUNGSINHALT" in resume_pdf
    assert b"PROJEKTINHALT" in resume_pdf


def test_compile_cv_inside_data_dir_hints_cd_dotdot(
    app_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "application-pipeline"
    data_dir.mkdir()
    (data_dir / "config.py").write_text("")
    monkeypatch.chdir(data_dir)

    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir)

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "inside the data directory" in err
    assert "cd .." in err


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
