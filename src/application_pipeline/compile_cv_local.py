from __future__ import annotations

from collections import deque
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque
from typing import Protocol


@dataclass(frozen=True, slots=True)
class _PdflatexRunResult:
    returncode: int
    log_text: str | None = None


class _PdflatexAdapter(Protocol):
    def run_pass(
        self,
        *,
        build_dir: Path,
        build_name: str,
        cv_data_dir: Path,
    ) -> _PdflatexRunResult: ...


@dataclass(frozen=True, slots=True)
class _CapturedPdflatexPass:
    cmd: list[str]
    cwd: Path
    capture_output: bool
    env: dict[str, str]


@dataclass(slots=True)
class _CompileCvFakePdflatexAdapter:
    outcomes: list[_PdflatexRunResult]
    captured_runs: list[_CapturedPdflatexPass] | None = None
    _queue: Deque[_PdflatexRunResult] = field(
        init=False,
        default_factory=deque,
    )

    def __post_init__(self) -> None:
        self._queue = deque(self.outcomes)

    def run_pass(
        self,
        *,
        build_dir: Path,
        build_name: str,
        cv_data_dir: Path,
    ) -> _PdflatexRunResult:
        cmd = self._pdflatex_cmd(build_name, cv_data_dir)
        if self.captured_runs is not None:
            self.captured_runs.append(
                _CapturedPdflatexPass(
                    cmd=cmd,
                    cwd=build_dir,
                    capture_output=True,
                    env={**os.environ, "TEXINPUTS": f".{os.pathsep}"},
                )
            )

        if not self._queue:
            raise AssertionError("unexpected pdflatex pass")
        result = self._queue.popleft()

        if result.returncode == 0:
            (build_dir / f"{build_name}.pdf").write_bytes(
                b"%PDF-1.4 fake\n" + build_name.encode("utf-8")
            )
        elif result.log_text is not None:
            (build_dir / f"{build_name}.log").write_text(
                result.log_text,
                encoding="utf-8",
            )

        return result

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
