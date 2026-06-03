from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class _PdflatexRunResult:
    returncode: int


class _PdflatexAdapter(Protocol):
    def run_pass(
        self,
        *,
        build_dir: Path,
        build_name: str,
        cv_data_dir: Path,
    ) -> _PdflatexRunResult: ...


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
        tex_input = (
            rf"\def\CvDataDir{{{cv_data_dir.as_posix()}}}"
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
