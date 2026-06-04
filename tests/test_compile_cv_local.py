from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from application_pipeline.compile_cv_local import (
    _CompileCvLocalProductionAdapter,
    _PdflatexRunResult,
)


@pytest.mark.parametrize("build_name", ["cover", "resume", "combined"])
def test_compile_cv_local_production_adapter_preserves_pdflatex_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    build_name: str,
) -> None:
    build_dir = tmp_path / ".build"
    build_dir.mkdir()
    cv_data_dir = tmp_path / "project" / "application-pipeline" / "user-info" / "cv"
    adapter = _CompileCvLocalProductionAdapter()
    captured: dict[str, object] = {}
    monkeypatch.setenv("KEEP_ME", "value")
    monkeypatch.setenv("TEXINPUTS", "host-tex")

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path,
        capture_output: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["capture_output"] = capture_output
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 7, stdout=b"", stderr=b"")

    monkeypatch.setattr(
        "application_pipeline.compile_cv_local.subprocess.run", fake_run
    )

    result = adapter.run_pass(
        build_dir=build_dir,
        build_name=build_name,
        cv_data_dir=cv_data_dir,
    )

    assert result == _PdflatexRunResult(returncode=7)
    assert captured["cmd"] == [
        "pdflatex",
        "-interaction=nonstopmode",
        "-jobname",
        build_name,
        rf"\def\CvDataDir{{{cv_data_dir.as_posix()}}}\def\BUILD{{{build_name}}}\input{{cv}}",
    ]
    assert captured["cwd"] == build_dir
    assert captured["capture_output"] is True
    assert captured["env"] == {
        **os.environ,
        "TEXINPUTS": f".{os.pathsep}",
    }
    assert os.environ["TEXINPUTS"] == "host-tex"
