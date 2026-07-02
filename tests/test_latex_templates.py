"""Structural contract tests for the LaTeX template files."""

from __future__ import annotations

import importlib.resources
import re
import shutil
from pathlib import Path

import pytest

from application_pipeline.compile_cv_cmd import _CompileCvWorkflow, compile_cv
from application_pipeline.compile_cv_local import _PdflatexRunResult
from application_pipeline.cv_slot_contract import SLOT_NAMES, TEMPLATE_MARKER_SET

_EXPECTED_LATEX_PACKAGE_FILES = frozenset(
    {
        "cv_template.tex",
        "slot_map.py",
        # Vendored moderncv 1.2.0 distribution (ADR-0034).
        "moderncv.cls",
        "moderncvcolorblack.sty",
        "moderncvcolorblue.sty",
        "moderncvcolorgreen.sty",
        "moderncvcolorgrey.sty",
        "moderncvcolororange.sty",
        "moderncvcolorpurple.sty",
        "moderncvcolorred.sty",
        "moderncvcompatibility.sty",
        "moderncvstylebanking.sty",
        "moderncvstylecasual.sty",
        "moderncvstyleclassic.sty",
        "moderncvstyleempty.sty",
        "moderncvstyleoldstyle.sty",
        "tweaklist.sty",
    }
)

_IDENTITY_TOKENS = (
    "<<ADDRESS_STREET>>",
    "<<ADDRESS_CITY>>",
    "<<PHONE>>",
    "<<EMAIL>>",
    "<<GITHUB_URL>>",
    "<<LINKEDIN_URL>>",
)

_RECIPIENT_PATTERN = re.compile(
    r"\\recipient\{\s*<<RECIPIENT_COMPANY>>\s*\}\{.*?"
    r"<<RECIPIENT_NAME>>.*?"
    r"<<RECIPIENT_STREET>>.*?"
    r"<<RECIPIENT_ZIP_CITY>>.*?\}",
    re.DOTALL,
)
_COVER_BODY_PATTERN = re.compile(
    r"\\AutoCoverLetterStretch\{[^}]+\}\{[^}]+\}\{[^}]+\}\{[^}]+\}\{.*?"
    r"<<COVER_INTRO>>.*?"
    r"<<COVER_BULLETS>>.*?"
    r"<<COVER_CLOSING>>.*?\}",
    re.DOTALL,
)
_RESUME_PATTERN = re.compile(
    r"\\section\{Ausbildung\}\s*<<RESUME_AUSBILDUNG>>.*?"
    r"\\section\{Projekte\}\s*<<RESUME_PROJEKTE>>.*?"
    r"\\section\{Berufserfahrung\}\s*<<RESUME_BERUFSERFAHRUNG>>.*?"
    r"\\section\{Kenntnisse\}\s*<<SKILLS_BLOCK>>",
    re.DOTALL,
)
_TEMPLATE_SLOT_PATTERN = re.compile(r"<<([A-Z_]+)>>")
_MINIMAL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
)


@pytest.fixture(scope="module")
def cv_template() -> str:
    return (
        importlib.resources.files("application_pipeline.latex") / "cv_template.tex"
    ).read_text(encoding="utf-8")


def assert_template_contract(template: str) -> None:
    leaked = [t for t in _IDENTITY_TOKENS if t in template]
    assert leaked == [], f"cv_template.tex leaks identity tokens: {leaked}"
    actual_markers = {
        match.group(0) for match in _TEMPLATE_SLOT_PATTERN.finditer(template)
    }
    assert actual_markers == TEMPLATE_MARKER_SET
    assert _RECIPIENT_PATTERN.search(template)
    assert re.search(r"\\opening\{.*?<<OPENING>>", template, re.DOTALL)
    assert _COVER_BODY_PATTERN.search(template)
    assert _RESUME_PATTERN.search(template)


def assert_compiled_template_contract(
    compiled_template: str,
    slot_bodies: dict[str, str],
) -> None:
    leaked = [t for t in _IDENTITY_TOKENS if t in compiled_template]
    assert leaked == [], f"compiled cv.tex leaks identity tokens: {leaked}"
    assert "<<" not in compiled_template

    for slot_body in slot_bodies.values():
        assert slot_body in compiled_template

    recipient = re.compile(
        r"\\recipient\{\s*"
        + re.escape(slot_bodies["recipient_company"])
        + r"\s*\}\{.*?"
        + re.escape(slot_bodies["recipient_name"])
        + r".*?"
        + re.escape(slot_bodies["recipient_street"])
        + r".*?"
        + re.escape(slot_bodies["recipient_zip_city"])
        + r".*?\}",
        re.DOTALL,
    )
    cover = re.compile(
        r"\\opening\{.*?"
        + re.escape(slot_bodies["cover_subject"])
        + r".*?"
        + re.escape(slot_bodies["opening"])
        + r".*?\}.*?"
        + re.escape(slot_bodies["cover_intro"])
        + r".*?"
        + re.escape(slot_bodies["cover_bullets"])
        + r".*?"
        + re.escape(slot_bodies["cover_closing"]),
        re.DOTALL,
    )
    resume = re.compile(
        r"\\section\{Ausbildung\}\s*"
        + re.escape(slot_bodies["resume_ausbildung"])
        + r".*?\\section\{Projekte\}\s*"
        + re.escape(slot_bodies["resume_projekte"])
        + r".*?\\section\{Berufserfahrung\}\s*"
        + re.escape(slot_bodies["resume_berufserfahrung"])
        + r".*?\\section\{Kenntnisse\}\s*"
        + re.escape(slot_bodies["skills_block"]),
        re.DOTALL,
    )

    assert recipient.search(compiled_template)
    assert cover.search(compiled_template)
    assert resume.search(compiled_template)


def _compileable_slot_bodies() -> dict[str, str]:
    return dict(
        zip(
            SLOT_NAMES,
            (
                "Firma GmbH",
                "Frau Dr. Muller",
                "Musterstrasse 1",
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


def test_cv_template_matches_structural_contract(cv_template: str) -> None:
    assert_template_contract(cv_template)


def test_latex_package_ships_vendored_moderncv_tree() -> None:
    """The whole moderncv 1.2.0 distro (per ADR-0034) ships with the package."""
    pkg = importlib.resources.files("application_pipeline.latex")
    actual = {item.name for item in pkg.iterdir() if not item.name.startswith("__")}
    missing = _EXPECTED_LATEX_PACKAGE_FILES - actual
    unexpected = actual - _EXPECTED_LATEX_PACKAGE_FILES
    assert missing == set(), f"missing vendored files: {missing}"
    assert unexpected == set(), f"unexpected files in latex package: {unexpected}"


def test_cv_template_contract_tolerates_spacing_and_comment_changes(
    cv_template: str,
) -> None:
    structurally_equivalent = cv_template.replace(
        r"\closing{Mit freundlichen Grüßen,}",
        "% harmless comment\n\\closing{Mit freundlichen Grüßen,}",
    ).replace(
        "\n\n<<COVER_INTRO>>\n\n",
        "\n% harmless comment between paragraphs\n<<COVER_INTRO>>\n",
    )

    assert_template_contract(structurally_equivalent)


def test_cv_template_contract_rejects_unexpected_slot_markers(
    cv_template: str,
) -> None:
    with pytest.raises(AssertionError):
        assert_template_contract(f"{cv_template}\n<<COVER_STRETCH>>\n")


def test_cv_template_contract_rejects_renamed_slot_markers(cv_template: str) -> None:
    with pytest.raises(AssertionError):
        assert_template_contract(
            cv_template.replace("<<COVER_BULLETS>>", "<<COVER_MATCH>>", 1)
        )


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_compile_cv_template_is_compileable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    app_dir = tmp_path / "application"
    cv_dir = project_root / "application-pipeline" / "user-info" / "cv"
    (project_root / "application-pipeline").mkdir(parents=True)
    (project_root / "application-pipeline" / "config.py").write_text("")
    cv_dir.mkdir(parents=True)
    app_dir.mkdir()
    monkeypatch.chdir(project_root)

    (cv_dir / "facts.tex").write_text(
        "\n".join(
            (
                r"\def\myFirstname{Test}",
                r"\def\myFamilyname{User}",
                r"\def\myCity{Berlin}",
                r"\def\PersonalInfo{}",
                r"\def\Languages{}",
                r"\def\Hobbies{}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (cv_dir / "content_pool.tex").write_text("", encoding="utf-8")
    (cv_dir / "profile.png").write_bytes(_MINIMAL_PNG)
    (cv_dir / "signature.png").write_bytes(_MINIMAL_PNG)
    slot_bodies = _compileable_slot_bodies()
    app_dir.joinpath("cv.tex").write_text(
        "".join(f"%% SLOT: {name}\n{slot_bodies[name]}\n" for name in SLOT_NAMES),
        encoding="utf-8",
    )

    compile_cv(app_dir)

    assert (app_dir / "cover_application.pdf").exists()
    assert (app_dir / "resume_application.pdf").exists()
    assert (app_dir / "combined_application.pdf").exists()
    assert not (app_dir / "cover.pdf").exists()
    assert not (app_dir / "resume.pdf").exists()
    assert not (app_dir / "combined.pdf").exists()


def test_compile_cv_wires_slot_map_content_into_structural_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    app_dir = tmp_path / "application"
    cv_dir = project_root / "application-pipeline" / "user-info" / "cv"
    (project_root / "application-pipeline").mkdir(parents=True)
    (project_root / "application-pipeline" / "config.py").write_text("")
    cv_dir.mkdir(parents=True)
    app_dir.mkdir()
    monkeypatch.chdir(project_root)

    (cv_dir / "facts.tex").write_text(
        "\n".join(
            (
                r"\def\myFirstname{Test}",
                r"\def\myFamilyname{User}",
                r"\def\myCity{Berlin}",
                r"\def\PersonalInfo{}",
                r"\def\Languages{}",
                r"\def\Hobbies{}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (cv_dir / "content_pool.tex").write_text("", encoding="utf-8")

    slot_bodies = {name: f"slot-body-{name}" for name in SLOT_NAMES}
    app_dir.joinpath("cv.tex").write_text(
        "".join(f"%% SLOT: {name}\n{body}\n" for name, body in slot_bodies.items()),
        encoding="utf-8",
    )

    class _FailingPdflatexAdapter:
        def run_pass(
            self,
            *,
            build_dir: Path,
            build_name: str,
            cv_data_dir: Path,
        ) -> _PdflatexRunResult:
            return _PdflatexRunResult(returncode=1)

    with pytest.raises(SystemExit):
        _CompileCvWorkflow(
            app_dir=app_dir,
            pdflatex=_FailingPdflatexAdapter(),
        ).run()

    build_cv = (app_dir / ".build" / "cv.tex").read_text(encoding="utf-8")
    assert_compiled_template_contract(build_cv, slot_bodies)
