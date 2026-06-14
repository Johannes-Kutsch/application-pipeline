"""Smoke test — end-to-end compile-cv regression.

Marked @pytest.mark.smoke so it is excluded from the default offline run
(see pyproject.toml: addopts = "-m 'not smoke'").
Run explicitly with: pytest -m smoke
"""

from __future__ import annotations

import shutil
import struct
import zlib
from pathlib import Path

import pytest

from application_pipeline.compile_cv_cmd import compile_cv
from application_pipeline.cv_slot_contract import SLOT_NAMES


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


def _require_pdflatex() -> None:
    """Skip the test if pdflatex is absent."""
    if not shutil.which("pdflatex"):
        pytest.skip("pdflatex not found — install TeX Live or MiKTeX")


def _cv_tex() -> str:
    bodies = dict(
        zip(
            SLOT_NAMES,
            (
                "Smoke Test GmbH",
                "Frau Test",
                "Teststrasse 1",
                "12345 Berlin",
                "Sehr geehrte Damen und Herren,",
                "Placeholder intro.",
                "Placeholder pivot.",
                "Placeholder fit.",
                "Placeholder closing.",
                r"\cventry{2020--2023}{Developer}{Firma}{Berlin}{}{}",
                r"\cventry{2016--2020}{B.Sc.}{TU Berlin}{Berlin}{}{}",
                r"\cventry{2021}{Projekt}{}{}{}{Beschreibung}",
                "Python, LaTeX",
            ),
            strict=True,
        )
    )
    return "".join(f"%% SLOT: {name}\n{bodies[name]}\n" for name in SLOT_NAMES)


def _facts_tex() -> str:
    return (
        r"\def\myFirstname{Test}" + "\n"
        r"\def\myFamilyname{User}" + "\n"
        r"\def\myCity{Berlin}" + "\n"
        r"\def\PersonalInfo{\cvitem{Adresse}{Teststrasse 1, 12345 Berlin}}" + "\n"
        r"\def\Languages{\begin{itemize}\item Deutsch\end{itemize}}" + "\n"
        r"\def\Hobbies{\begin{itemize}\item Programmieren\end{itemize}}" + "\n"
    )


@pytest.fixture()
def smoke_app_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """App dir and project root wired for a real pdflatex compile."""
    project_root = tmp_path / "project"
    app_dir = tmp_path / "application"
    cv_dir = project_root / "application-pipeline" / "user-info" / "cv"

    (project_root / "application-pipeline").mkdir(parents=True)
    (project_root / "application-pipeline" / "config.py").write_text("")
    cv_dir.mkdir(parents=True)
    app_dir.mkdir()
    monkeypatch.chdir(project_root)

    (cv_dir / "facts.tex").write_text(_facts_tex(), encoding="utf-8")
    (cv_dir / "content_pool.tex").write_text("", encoding="utf-8")

    png = _minimal_png()
    (cv_dir / "profile.png").write_bytes(png)
    (cv_dir / "signature.png").write_bytes(png)

    (app_dir / "cv.tex").write_text(_cv_tex(), encoding="utf-8")
    return app_dir


@pytest.mark.smoke
def test_compile_cv_end_to_end_produces_pdfs_and_cleans_build(
    smoke_app_dir: Path,
) -> None:
    _require_pdflatex()

    compile_cv(smoke_app_dir)

    assert (smoke_app_dir / "cover_application.pdf").exists()
    assert (smoke_app_dir / "resume_application.pdf").exists()
    assert (smoke_app_dir / "combined_application.pdf").exists()
    assert not (smoke_app_dir / "cover.pdf").exists()
    assert not (smoke_app_dir / "resume.pdf").exists()
    assert not (smoke_app_dir / "combined.pdf").exists()
    assert not (smoke_app_dir / ".build").exists()
