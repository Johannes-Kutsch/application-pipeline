"""Smoke test — end-to-end compile-cv regression.

Marked @pytest.mark.smoke so it is excluded from the default offline run
(see pyproject.toml: addopts = "-m 'not smoke'").
Run explicitly with: pytest -m smoke
"""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from application_pipeline.compile_cv_cmd import compile_cv


def _minimal_png() -> bytes:
    """Return bytes of a 1×1 white RGB PNG."""

    def _chunk(name: bytes, data: bytes) -> bytes:
        body = name + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    iend = _chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def _require_pdflatex_and_moderncv() -> None:
    """Skip the test if pdflatex or a sufficiently new moderncv is absent."""
    if not shutil.which("pdflatex"):
        pytest.skip("pdflatex not found — install TeX Live or MiKTeX")

    result = subprocess.run(
        ["kpsewhich", "moderncv.cls"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        pytest.skip(
            "moderncv not installed — install texlive-latex-extra or equivalent"
        )

    cls_text = Path(result.stdout.strip()).read_text(errors="replace")
    m = re.search(r"\\ProvidesClass\{moderncv\}\[(\d{4})/(\d{2})/(\d{2})", cls_text)
    if not m:
        pytest.skip("moderncv .cls found but version date is unreadable")

    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if (year, month, day) <= (2015, 1, 1):
        pytest.skip(
            f"moderncv {year}/{month:02d}/{day:02d} is older than 2.0.0 "
            "— install TeX Live 2020+ or MiKTeX rolling"
        )


def _cv_tex() -> str:
    slots = [
        ("recipient_company", "Smoke Test GmbH"),
        ("recipient_name", "Frau Test"),
        ("recipient_street", "Teststrasse 1"),
        ("recipient_zip_city", "12345 Berlin"),
        ("opening", "Sehr geehrte Damen und Herren,"),
        ("cover_intro", "Placeholder intro."),
        ("cover_pivot", "Placeholder pivot."),
        ("cover_fit", "Placeholder fit."),
        ("cover_closing", "Placeholder closing."),
        (
            "resume_berufserfahrung",
            r"\cventry{2020--2023}{Developer}{Firma}{Berlin}{}{}",
        ),
        ("resume_ausbildung", r"\cventry{2016--2020}{B.Sc.}{TU Berlin}{Berlin}{}{}"),
        ("resume_projekte", r"\cventry{2021}{Projekt}{}{}{}{Beschreibung}"),
        ("skills_block", "Python, LaTeX"),
    ]
    return "".join(f"%% SLOT: {name}\n{body}\n" for name, body in slots)


def _facts_tex() -> str:
    return (
        r"\def\myFirstname{Test}" + "\n"
        r"\def\myFamilyname{User}" + "\n"
        r"\def\myStreet{Teststrasse 1}" + "\n"
        r"\def\myZip{12345 Berlin}" + "\n"
        r"\def\myPhone{+49 30 12345678}" + "\n"
        r"\def\myEmail{test@example.com}" + "\n"
        r"\def\myGithub{testuser}" + "\n"
        r"\def\myLinkedin{testuser}" + "\n"
        r"\def\Languages{\begin{itemize}\item Deutsch\end{itemize}}" + "\n"
        r"\def\Hobbies{\begin{itemize}\item Programmieren\end{itemize}}" + "\n"
    )


@pytest.fixture()
def smoke_app_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """App dir and project root wired for a real pdflatex compile."""
    project_root = tmp_path / "project"
    app_dir = tmp_path / "application"
    user_info = project_root / "application-pipeline" / "user-info"

    (project_root / "application-pipeline").mkdir(parents=True)
    (project_root / "application-pipeline" / "config.py").write_text("")
    user_info.mkdir(parents=True)
    app_dir.mkdir()
    monkeypatch.chdir(project_root)

    (user_info / "facts.tex").write_text(_facts_tex(), encoding="utf-8")
    (user_info / "content_pool.tex").write_text("", encoding="utf-8")

    png = _minimal_png()
    (user_info / "profile.png").write_bytes(png)
    (user_info / "signature.png").write_bytes(png)

    (app_dir / "cv.tex").write_text(_cv_tex(), encoding="utf-8")
    return app_dir


@pytest.mark.smoke
def test_compile_cv_end_to_end_produces_pdfs_and_cleans_build(
    smoke_app_dir: Path,
) -> None:
    _require_pdflatex_and_moderncv()

    compile_cv(smoke_app_dir)

    assert (smoke_app_dir / "cover.pdf").exists()
    assert (smoke_app_dir / "resume.pdf").exists()
    assert (smoke_app_dir / "combined.pdf").exists()
    assert not (smoke_app_dir / ".build").exists()
