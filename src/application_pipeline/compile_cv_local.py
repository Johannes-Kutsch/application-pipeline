from __future__ import annotations

from collections import deque
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque
from typing import Protocol

from application_pipeline.cv_slot_contract import COVER_PARAGRAPH_PATTERN_SLOTS
from application_pipeline.latex.slot_map import parse


@dataclass(frozen=True, slots=True)
class _PdflatexRunResult:
    returncode: int
    log_text: str | None = None
    page_count: int | None = None


class _PdflatexAdapter(Protocol):
    def run_pass(
        self,
        *,
        build_dir: Path,
        build_name: str,
        cv_data_dir: Path,
    ) -> _PdflatexRunResult: ...


@dataclass(slots=True)
class _CompileCvFakePdflatexAdapter:
    outcomes: list[_PdflatexRunResult]
    _queue: Deque[_PdflatexRunResult] = field(
        init=False,
        default_factory=deque,
    )
    _slot_map: dict[str, str] | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._queue = deque(self.outcomes)

    def run_pass(
        self,
        *,
        build_dir: Path,
        build_name: str,
        cv_data_dir: Path,
    ) -> _PdflatexRunResult:
        if not self._queue:
            raise AssertionError("unexpected pdflatex pass")
        result = self._queue.popleft()

        if result.returncode == 0:
            if self._slot_map is None:
                self._slot_map = parse(build_dir.parent / "cv.tex")
            _assert_substituted_slots_present(
                build_dir / "cv.tex",
                self._slot_map,
                _fake_pdf_slot_names(build_name),
            )
            (build_dir / f"{build_name}.pdf").write_bytes(
                b"%PDF-1.4 fake\n"
                + build_name.encode("utf-8")
                + b"\n"
                + b"".join(
                    self._slot_map[slot].rstrip("\n").encode("utf-8")
                    for slot in _fake_pdf_slot_names(build_name)
                )
            )
        elif result.log_text is not None:
            (build_dir / f"{build_name}.log").write_text(
                result.log_text,
                encoding="utf-8",
            )

        return result


def _fake_pdf_slot_names(build_name: str) -> tuple[str, ...]:
    if build_name == "cover":
        return (
            "recipient_company",
            "recipient_name",
            "recipient_street",
            "recipient_zip_city",
            "opening",
            *COVER_PARAGRAPH_PATTERN_SLOTS,
        )
    if build_name == "resume":
        return (
            "resume_berufserfahrung",
            "resume_ausbildung",
            "resume_projekte",
            "skills_block",
        )
    return (
        "recipient_company",
        "recipient_name",
        "recipient_street",
        "recipient_zip_city",
        "opening",
        *COVER_PARAGRAPH_PATTERN_SLOTS,
        "resume_berufserfahrung",
        "resume_ausbildung",
        "resume_projekte",
        "skills_block",
    )


def _assert_substituted_slots_present(
    build_cv_tex: Path,
    slot_map: dict[str, str],
    slot_names: tuple[str, ...],
) -> None:
    build_text = build_cv_tex.read_text(encoding="utf-8")
    for slot_name in slot_names:
        if slot_map[slot_name].rstrip("\n") not in build_text:
            raise AssertionError(f"staged cv.tex missing slot body: {slot_name}")


@dataclass(frozen=True, slots=True)
class _CompileCvLocalProductionAdapter:
    def run_pass(
        self,
        *,
        build_dir: Path,
        build_name: str,
        cv_data_dir: Path,
    ) -> _PdflatexRunResult:
        result = subprocess.run(
            self._pdflatex_cmd(build_name, cv_data_dir),
            cwd=build_dir,
            capture_output=True,
            env={**os.environ, "TEXINPUTS": f".{os.pathsep}"},
        )
        return _PdflatexRunResult(returncode=result.returncode)

    def _pdflatex_cmd(self, build_name: str, cv_data_dir: Path) -> list[str]:
        cv_data_dir_tex = cv_data_dir.as_posix().replace("\\", "/")
        tex_input = (
            rf"\def\CvDataDir{{{cv_data_dir_tex}}}"
            rf"\def\BUILD{{{build_name}}}"
            r"\input{cv}"
        )
        return [
            "pdflatex",
            "-interaction=nonstopmode",
            "-jobname",
            build_name,
            tex_input,
        ]
