"""Tests for the compile-cv subcommand."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import application_pipeline.compile_cv_cmd as compile_cv_cmd_module
from application_pipeline.__main__ import main
from application_pipeline.compile_cv_cmd import _CompileCvWorkflow, compile_cv
from application_pipeline.compile_cv_local import _PdflatexAdapter, _PdflatexRunResult
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
class _PdflatexCall:
    build_dir: Path
    build_name: str
    cv_data_dir: Path


@dataclass(frozen=True, slots=True)
class _PassingPdflatexPass:
    def run(self, call: _PdflatexCall) -> _PdflatexRunResult:
        _write_fake_pdf(call.build_dir, call.build_name)
        return _PdflatexRunResult(returncode=0)


@dataclass(frozen=True, slots=True)
class _FailingPdflatexPass:
    log_text: str | None = None

    def run(self, call: _PdflatexCall) -> _PdflatexRunResult:
        if self.log_text is not None:
            (call.build_dir / f"{call.build_name}.log").write_text(
                self.log_text,
                encoding="utf-8",
            )
        return _PdflatexRunResult(returncode=1)


@dataclass(slots=True)
class _FakePdflatexAdapter(_PdflatexAdapter):
    passes: list[_PassingPdflatexPass | _FailingPdflatexPass]
    calls: list[_PdflatexCall]

    def run_pass(
        self,
        *,
        build_dir: Path,
        build_name: str,
        cv_data_dir: Path,
    ) -> _PdflatexRunResult:
        call = _PdflatexCall(
            build_dir=build_dir,
            build_name=build_name,
            cv_data_dir=cv_data_dir,
        )
        self.calls.append(call)
        if not self.passes:
            raise AssertionError("unexpected pdflatex pass")
        return self.passes.pop(0).run(call)


def _passing_pdflatex_adapter() -> _FakePdflatexAdapter:
    return _FakePdflatexAdapter(
        passes=[_PassingPdflatexPass() for _ in range(6)],
        calls=[],
    )


def _failing_pdflatex_adapter(*, log_text: str | None = None) -> _FakePdflatexAdapter:
    return _FakePdflatexAdapter(
        passes=[_FailingPdflatexPass(log_text=log_text)],
        calls=[],
    )


def _run_compile_workflow(app_dir: Path, pdflatex: _PdflatexAdapter) -> None:
    _CompileCvWorkflow(
        app_dir=app_dir,
        pdflatex=pdflatex,
    ).run()


def _published_pdf(app_dir: Path, build_name: str) -> Path:
    return app_dir / f"{build_name}_{app_dir.name}.pdf"


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
    _run_compile_workflow(app_dir, _passing_pdflatex_adapter())

    assert (app_dir / "cover_application.pdf").exists()
    assert (app_dir / "resume_application.pdf").exists()
    assert (app_dir / "combined_application.pdf").exists()
    assert not (app_dir / "cover.pdf").exists()
    assert not (app_dir / "resume.pdf").exists()
    assert not (app_dir / "combined.pdf").exists()


def test_compile_cv_supported_build_modes_include_slot_content(
    app_dir: Path,
    project_root: Path,
) -> None:
    _run_compile_workflow(app_dir, _passing_pdflatex_adapter())

    assert b"Ich bewerbe mich hiermit." in _published_pdf(app_dir, "cover").read_bytes()
    assert b"Developer" in _published_pdf(app_dir, "resume").read_bytes()
    assert b"Python, LaTeX" in _published_pdf(app_dir, "combined").read_bytes()


def test_compile_cv_publishes_distinct_cover_resume_and_combined_outputs(
    app_dir: Path,
    project_root: Path,
) -> None:
    _run_compile_workflow(app_dir, _passing_pdflatex_adapter())

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
) -> None:
    _run_compile_workflow(app_dir, _passing_pdflatex_adapter())

    assert not (app_dir / ".build").exists()


def test_compile_cv_overwrites_existing_pdfs(
    app_dir: Path,
    project_root: Path,
) -> None:
    for name in ("cover", "resume", "combined"):
        _published_pdf(app_dir, name).write_bytes(b"stale")
        (app_dir / f"{name}.pdf").write_bytes(b"stale-generic")
    _run_compile_workflow(app_dir, _passing_pdflatex_adapter())

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

    adapter = _passing_pdflatex_adapter()
    _run_compile_workflow(app_dir, adapter)

    expected_cv_data_dir = (
        (project_root / "application-pipeline" / "user-info" / "cv")
        .resolve()
        .as_posix()
    )
    assert any(
        call.cv_data_dir.as_posix() == expected_cv_data_dir for call in adapter.calls
    )
    assert not any(
        call.cv_data_dir.as_posix() == irrelevant.resolve().as_posix()
        for call in adapter.calls
    )


def test_compile_cv_exits_nonzero_on_first_failure(
    app_dir: Path,
    project_root: Path,
) -> None:
    adapter = _failing_pdflatex_adapter()

    with pytest.raises(SystemExit) as exc_info:
        _run_compile_workflow(app_dir, adapter)

    assert exc_info.value.code != 0
    assert len(adapter.calls) == 1, "should stop after first failure"


def test_compile_cv_leaves_build_dir_on_failure(
    app_dir: Path,
    project_root: Path,
) -> None:
    with pytest.raises(SystemExit):
        _run_compile_workflow(app_dir, _failing_pdflatex_adapter())

    assert (app_dir / ".build").exists()


def test_compile_cv_does_not_write_pdfs_to_dir_on_failure(
    app_dir: Path,
    project_root: Path,
) -> None:
    with pytest.raises(SystemExit):
        _run_compile_workflow(app_dir, _failing_pdflatex_adapter())

    for name in ("cover", "resume", "combined"):
        assert not (app_dir / f"{name}.pdf").exists()
        assert not _published_pdf(app_dir, name).exists()


def test_compile_cv_emits_error_blob_to_stderr_on_failure(
    app_dir: Path,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        _run_compile_workflow(
            app_dir,
            _failing_pdflatex_adapter(
                log_text=(
                    "This is pdflatex\n"
                    "! Undefined control sequence.\n"
                    "l.42 \\badmacro\n"
                    "           {foo}\n"
                    "? \n"
                )
            ),
        )

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

    adapter = _passing_pdflatex_adapter()
    with pytest.raises(SystemExit) as exc_info:
        _run_compile_workflow(app_dir, adapter)

    assert exc_info.value.code == 2
    assert "inside the data directory" in capsys.readouterr().err
    assert adapter.calls == []
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

    adapter = _passing_pdflatex_adapter()
    with pytest.raises(SystemExit) as exc_info:
        _run_compile_workflow(app_dir, adapter)

    assert exc_info.value.code != 0
    assert "did you forget to run /write-cv?" in capsys.readouterr().err
    assert adapter.calls == []
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
        "\n".join(
            (
                "%% SLOT: recipient_company",
                "Firma GmbH",
                "%% SLOT: recipient_name",
                "Frau Dr. Muller",
                "%% SLOT: recipient_street",
                "Musterstrasse 1",
                "%% SLOT: recipient_zip_city",
                "12345 Berlin",
                "%% SLOT: opening",
                "Sehr geehrte Damen und Herren,",
                "%% SLOT: cover_intro",
                "Ich bewerbe mich hiermit.",
                "%% SLOT: cover_pivot",
                "Mein Hintergrund ist relevant.",
                "%% SLOT: cover_fit",
                "Ich passe gut zu Ihrer Firma.",
                "%% SLOT: cover_closing",
                "Ich freue mich auf Ihre Antwort.",
                "%% SLOT: resume_berufserfahrung",
                r"\cventry{2020--2023}{Developer}{Firma}{Berlin}{}{}",
                "%% SLOT: resume_ausbildung",
                r"\cventry{2016--2020}{B.Sc.}{TU Berlin}{Berlin}{}{}",
                "%% SLOT: skills_block",
                "Python, LaTeX",
                "",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    adapter = _passing_pdflatex_adapter()
    with pytest.raises(SystemExit) as exc_info:
        _run_compile_workflow(app_dir, adapter)

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "missing slots: resume_projekte" in err
    assert adapter.calls == []
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
) -> None:
    adapter = _passing_pdflatex_adapter()
    _run_compile_workflow(app_dir, adapter)

    expected_cv_data_dir = (
        (project_root / "application-pipeline" / "user-info" / "cv")
        .resolve()
        .as_posix()
    )
    assert any(
        call.cv_data_dir.as_posix() == expected_cv_data_dir for call in adapter.calls
    )


def test_compile_cv_runs_two_passes_for_each_build_mode(
    app_dir: Path,
    project_root: Path,
) -> None:
    adapter = _passing_pdflatex_adapter()

    _run_compile_workflow(app_dir, adapter)

    assert [call.build_name for call in adapter.calls] == [
        "cover",
        "cover",
        "resume",
        "resume",
        "combined",
        "combined",
    ]


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


def test_compile_cv_cv_data_dir_uses_forward_slashes(
    app_dir: Path,
    project_root: Path,
) -> None:
    adapter = _passing_pdflatex_adapter()
    _run_compile_workflow(app_dir, adapter)

    assert adapter.calls, "no pdflatex calls captured"
    for call in adapter.calls:
        assert call.cv_data_dir.as_posix() == str(call.cv_data_dir).replace("\\", "/")


def test_compile_cv_three_resume_slots_independently_substituted(
    project_root: Path,
    tmp_path: Path,
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
    _run_compile_workflow(app_dir, _passing_pdflatex_adapter())

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
