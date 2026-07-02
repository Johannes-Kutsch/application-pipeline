"""Tests for the Compile CV Workflow."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import application_pipeline.compile_cv_cmd as compile_cv_cmd_module
from application_pipeline.__main__ import main
from application_pipeline.compile_cv_cmd import _CompileCvWorkflow, compile_cv
from application_pipeline.compile_cv_local import (
    _CompileCvFakePdflatexAdapter,
    _PdflatexAdapter,
    _PdflatexRunResult,
)
from application_pipeline.cv_slot_contract import SLOT_NAMES


def _install_fake_pdflatex(
    outcomes: list[_PdflatexRunResult],
) -> _CompileCvFakePdflatexAdapter:
    return _CompileCvFakePdflatexAdapter(
        outcomes=outcomes,
    )


def _install_passing_pdflatex() -> _CompileCvFakePdflatexAdapter:
    return _install_fake_pdflatex(
        [_PdflatexRunResult(returncode=0) for _ in range(6)],
    )


def _install_failing_pdflatex(
    *,
    log_text: str | None = None,
) -> _CompileCvFakePdflatexAdapter:
    return _install_fake_pdflatex(
        [_PdflatexRunResult(returncode=1, log_text=log_text)],
    )


def _run_compile_with_fake_pdflatex(
    app_dir: Path,
    *,
    pdflatex: _PdflatexAdapter,
) -> None:
    _CompileCvWorkflow(app_dir, pdflatex=pdflatex).run()


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
                "Betreff: Ihre Stellenanzeige Beispielrolle",
                "Ich bewerbe mich hiermit.",
                "Ich freue mich auf Ihre Antwort.",
                r"\begin{itemize}\item Fakt A.\end{itemize}",
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
) -> None:
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    assert (app_dir / "cover_application.pdf").exists()
    assert (app_dir / "resume_application.pdf").exists()
    assert (app_dir / "combined_application.pdf").exists()
    assert not (app_dir / "cover.pdf").exists()
    assert not (app_dir / "resume.pdf").exists()
    assert not (app_dir / "combined.pdf").exists()


def test_compile_cv_cover_build_contains_only_cover_slots(
    app_dir: Path,
    project_root: Path,
) -> None:
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    cover_pdf = _published_pdf(app_dir, "cover").read_bytes()
    assert b"Ich bewerbe mich hiermit." in cover_pdf
    assert b"\\cventry{2020--2023}" not in cover_pdf
    assert b"\\cventry{2016--2020}" not in cover_pdf
    assert b"\\cventry{2021}{Projekt}" not in cover_pdf


def test_compile_cv_resume_build_contains_resume_slots_only(
    app_dir: Path,
    project_root: Path,
) -> None:
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    resume_pdf = _published_pdf(app_dir, "resume").read_bytes()
    assert b"\\cventry{2020--2023}" in resume_pdf
    assert b"\\cventry{2016--2020}" in resume_pdf
    assert b"\\cventry{2021}{Projekt}" in resume_pdf
    assert b"Python, LaTeX" in resume_pdf
    assert b"Ich bewerbe mich hiermit." not in resume_pdf
    assert b"Sehr geehrte Damen und Herren," not in resume_pdf


def test_compile_cv_combined_build_contains_cover_and_resume_slots(
    app_dir: Path,
    project_root: Path,
) -> None:
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    combined_pdf = _published_pdf(app_dir, "combined").read_bytes()
    assert b"Ich bewerbe mich hiermit." in combined_pdf
    assert b"\\cventry{2020--2023}" in combined_pdf
    assert b"Python, LaTeX" in combined_pdf
    assert b"\\cventry{2016--2020}" in combined_pdf
    assert b"\\cventry{2021}{Projekt}" in combined_pdf


def test_compile_cv_supported_build_modes_include_slot_content(
    app_dir: Path,
    project_root: Path,
) -> None:
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    assert b"cover" in _published_pdf(app_dir, "cover").read_bytes()
    assert b"resume" in _published_pdf(app_dir, "resume").read_bytes()
    assert b"combined" in _published_pdf(app_dir, "combined").read_bytes()


def test_compile_cv_publishes_distinct_cover_resume_and_combined_outputs(
    app_dir: Path,
    project_root: Path,
) -> None:
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    cover_pdf = _published_pdf(app_dir, "cover").read_bytes()
    resume_pdf = _published_pdf(app_dir, "resume").read_bytes()
    combined_pdf = _published_pdf(app_dir, "combined").read_bytes()

    assert b"%PDF-1.4 fake\ncover" in cover_pdf
    assert b"%PDF-1.4 fake\nresume" not in cover_pdf

    assert b"%PDF-1.4 fake\nresume" in resume_pdf
    assert b"%PDF-1.4 fake\ncover" not in resume_pdf

    assert b"%PDF-1.4 fake\ncombined" in combined_pdf


def test_compile_cv_removes_build_dir_on_success(
    app_dir: Path,
    project_root: Path,
) -> None:
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    assert not (app_dir / ".build").exists()


def test_compile_cv_overwrites_existing_pdfs(
    app_dir: Path,
    project_root: Path,
) -> None:
    for name in ("cover", "resume", "combined"):
        _published_pdf(app_dir, name).write_bytes(b"stale")
        (app_dir / f"{name}.pdf").write_bytes(b"stale-generic")
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

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
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    assert (app_dir / "cover_application.pdf").exists()
    assert (app_dir / "resume_application.pdf").exists()
    assert (app_dir / "combined_application.pdf").exists()


def test_compile_cv_leaves_build_dir_on_failure(
    app_dir: Path,
    project_root: Path,
) -> None:
    fake = _install_failing_pdflatex()

    with pytest.raises(SystemExit):
        _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    assert (app_dir / ".build").exists()


def test_compile_cv_does_not_write_pdfs_to_dir_on_failure(
    app_dir: Path,
    project_root: Path,
) -> None:
    fake = _install_failing_pdflatex()

    with pytest.raises(SystemExit):
        _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    for name in ("cover", "resume", "combined"):
        assert not (app_dir / f"{name}.pdf").exists()
        assert not _published_pdf(app_dir, name).exists()


def test_compile_cv_emits_error_blob_to_stderr_on_failure(
    app_dir: Path,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _install_failing_pdflatex(
        log_text=(
            "This is pdflatex\n! Undefined control sequence.\nl.42 \\badmacro\n           {foo}\n? \n"
        )
    )

    with pytest.raises(SystemExit):
        _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    err = capsys.readouterr().err
    assert "! Undefined control sequence." in err
    assert "\\badmacro" in err


@dataclass(slots=True)
class _FailingPdflatexAdapterWithInMemoryLog:
    """Adapter seam variant that returns log text without touching build files."""

    returncode: int = 1
    log_text: str = "This is pdflatex\n! Undefined control sequence.\nl.42 \\badmacro\n           {foo}\n? \n"

    def run_pass(
        self,
        *,
        build_dir: Path,
        build_name: str,
        cv_data_dir: Path,
    ) -> _PdflatexRunResult:
        assert build_dir is not None
        assert build_name
        assert cv_data_dir is not None
        return _PdflatexRunResult(
            returncode=self.returncode,
            log_text=self.log_text,
        )


class _CompileCvPreflightTripwire:
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("preflight must not instantiate pdflatex adapter")


def _install_preflight_tripwire(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        compile_cv_cmd_module,
        "_CompileCvLocalProductionAdapter",
        _CompileCvPreflightTripwire,
    )


def test_compile_cv_emits_error_blob_from_adapter_log_text_when_no_log_file(
    app_dir: Path,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = _FailingPdflatexAdapterWithInMemoryLog()

    with pytest.raises(SystemExit):
        _run_compile_with_fake_pdflatex(app_dir, pdflatex=adapter)

    err = capsys.readouterr().err
    assert "! Undefined control sequence." in err
    assert "l.42 \\badmacro" in err


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
    _install_preflight_tripwire(monkeypatch)
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
    _install_preflight_tripwire(monkeypatch)
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
    _install_preflight_tripwire(monkeypatch)
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


def _valid_cv_tex() -> str:
    return _render_cv_tex(_slot_bodies())


def test_compile_cv_missing_cv_tex_exits_with_write_cv_message(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_dir_no_cv = tmp_path / "app_no_cv"
    app_dir_no_cv.mkdir()
    _install_preflight_tripwire(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir_no_cv)

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "/write-cv" in err


def test_compile_cv_malformed_cv_tex_exits_naming_missing_slot(
    project_root: Path,
    app_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (app_dir / "cv.tex").write_text(
        "%% SLOT: recipient_company\nFirma GmbH\n", encoding="utf-8"
    )
    _install_preflight_tripwire(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir)

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "resume_ausbildung" in err


def test_compile_cv_three_resume_slots_independently_substituted(
    project_root: Path,
    tmp_path: Path,
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
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    resume_pdf = _published_pdf(app_dir, "resume").read_bytes()
    assert b"%PDF-1.4 fake\nresume" in resume_pdf


def test_compile_cv_retains_substituted_cv_tex_when_build_fails(
    app_dir: Path,
    project_root: Path,
) -> None:
    fake = _install_failing_pdflatex(
        log_text=(
            "This is pdflatex\n! Undefined control sequence.\nl.42 \\\\badmacro\n           {foo}\n? \n"
        )
    )

    with pytest.raises(SystemExit):
        _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    staged_cv = (app_dir / ".build" / "cv.tex").read_text(encoding="utf-8")
    assert "<<" not in staged_cv
    assert ">>" not in staged_cv
    assert "Firma GmbH" in staged_cv
    assert "Ich bewerbe mich hiermit." in staged_cv
    assert "Python, LaTeX" in staged_cv


@dataclass(slots=True)
class _MutateSourceSlotMapAfterStagingAdapter:
    app_dir: Path
    delegate: _PdflatexAdapter
    _calls: int = 0

    def run_pass(
        self,
        *,
        build_dir: Path,
        build_name: str,
        cv_data_dir: Path,
    ) -> _PdflatexRunResult:
        result = self.delegate.run_pass(
            build_dir=build_dir,
            build_name=build_name,
            cv_data_dir=cv_data_dir,
        )
        self._calls += 1
        if self._calls == 1:
            self.app_dir.joinpath("cv.tex").write_text(
                _render_cv_tex(
                    _slot_bodies(
                        {
                            "cover_intro": "MUTATED COVER BODY",
                            "resume_berufserfahrung": "MUTATED RESUME BODY",
                            "skills_block": "MUTATED SKILLS BODY",
                        }
                    )
                ),
                encoding="utf-8",
            )
        return result


def test_compile_cv_published_pdfs_keep_staged_cv_tex_after_source_changes(
    app_dir: Path,
    project_root: Path,
) -> None:
    adapter = _MutateSourceSlotMapAfterStagingAdapter(
        app_dir=app_dir,
        delegate=_install_passing_pdflatex(),
    )

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=adapter)

    cover_pdf = _published_pdf(app_dir, "cover").read_bytes()
    resume_pdf = _published_pdf(app_dir, "resume").read_bytes()

    assert b"Ich bewerbe mich hiermit." in cover_pdf
    assert b"MUTATED COVER BODY" not in cover_pdf
    assert b"\\cventry{2020--2023}" in resume_pdf
    assert b"MUTATED RESUME BODY" not in resume_pdf
    assert b"MUTATED SKILLS BODY" not in resume_pdf


def test_compile_cv_published_pdfs_differ_by_build_name(
    app_dir: Path,
    project_root: Path,
) -> None:
    fake = _install_passing_pdflatex()

    _run_compile_with_fake_pdflatex(app_dir, pdflatex=fake)

    cover_pdf = _published_pdf(app_dir, "cover").read_bytes()
    resume_pdf = _published_pdf(app_dir, "resume").read_bytes()
    combined_pdf = _published_pdf(app_dir, "combined").read_bytes()

    assert cover_pdf != resume_pdf
    assert cover_pdf != combined_pdf
    assert resume_pdf != combined_pdf
    assert b"%PDF-1.4 fake\ncover" in cover_pdf
    assert b"%PDF-1.4 fake\nresume" in resume_pdf
    assert b"%PDF-1.4 fake\ncombined" in combined_pdf


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
    _install_preflight_tripwire(monkeypatch)

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
    _install_preflight_tripwire(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        compile_cv(app_dir)

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "no application-pipeline/config.py in" in err
    assert "did you forget to cd, or run init?" in err
    assert not (app_dir / ".build").exists()
