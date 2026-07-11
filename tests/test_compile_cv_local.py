"""Tests for the production pdflatex adapter invocation contract."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import application_pipeline.compile_cv_local as compile_cv_local_module


def _capture_subprocess_call(
    monkeypatch: pytest.MonkeyPatch, returncode: int = 0
) -> dict:
    captured: dict[str, object] = {}

    def _fake_run(
        cmd: list[str],
        *,
        cwd: Path,
        capture_output: bool,
        env: dict[str, str],
    ) -> SimpleNamespace:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["capture_output"] = capture_output
        captured["env"] = env
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(compile_cv_local_module.subprocess, "run", _fake_run)
    return captured


def test_pdflatex_adapter_invokes_pdflatex_with_required_tex_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_subprocess_call(monkeypatch)
    adapter = compile_cv_local_module._CompileCvLocalProductionAdapter()
    build_dir = tmp_path / ".build"
    cv_data_dir = Path(r"C:\Users\Example\Documents")

    result = adapter.run_pass(
        build_dir=build_dir,
        build_name="cover",
        cv_data_dir=cv_data_dir,
    )

    assert result.returncode == 0
    assert captured["cmd"] == [
        "pdflatex",
        "-interaction=nonstopmode",
        "-jobname",
        "cover",
        r"\def\CvDataDir{C:/Users/Example/Documents}\def\BUILD{cover}\input{cv}",
    ]


def test_pdflatex_adapter_runs_in_build_dir_and_captures_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_subprocess_call(monkeypatch)
    adapter = compile_cv_local_module._CompileCvLocalProductionAdapter()
    build_dir = tmp_path / ".build"

    adapter.run_pass(
        build_dir=build_dir,
        build_name="combined",
        cv_data_dir=Path("/tmp/cv-data"),
    )

    assert captured["cwd"] == build_dir
    assert captured["capture_output"] is True


def test_pdflatex_adapter_preserves_environment_except_texinputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEEP_ME", "value")
    monkeypatch.setenv("TEXINPUTS", "host:tex")
    captured = _capture_subprocess_call(monkeypatch)
    adapter = compile_cv_local_module._CompileCvLocalProductionAdapter()

    adapter.run_pass(
        build_dir=tmp_path / ".build",
        build_name="resume",
        cv_data_dir=Path("/tmp/cv-data"),
    )

    env = captured["env"]
    assert env is not None
    assert env["KEEP_ME"] == "value"
    assert env["TEXINPUTS"] == f".{os.pathsep}"
    assert os.environ["TEXINPUTS"] == "host:tex"


def test_pdflatex_adapter_returns_subprocess_returncode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_subprocess_call(monkeypatch, returncode=7)
    adapter = compile_cv_local_module._CompileCvLocalProductionAdapter()

    result = adapter.run_pass(
        build_dir=tmp_path / ".build",
        build_name="combined",
        cv_data_dir=Path("/tmp/cv-data"),
    )

    assert captured["cmd"][0] == "pdflatex"
    assert result == compile_cv_local_module._PdflatexRunResult(returncode=7)
    assert result.log_text is None
    assert result.page_count is None


def test_pdflatex_run_result_page_count_defaults_to_none() -> None:
    result = compile_cv_local_module._PdflatexRunResult(returncode=0)

    assert result.page_count is None


def test_pdflatex_run_result_accepts_explicit_page_count() -> None:
    result = compile_cv_local_module._PdflatexRunResult(returncode=0, page_count=3)

    assert result.page_count == 3


def test_fake_pdflatex_adapter_surfaces_configured_page_count_per_pass(
    tmp_path: Path,
) -> None:
    adapter = compile_cv_local_module._CompileCvFakePdflatexAdapter(
        outcomes=[
            compile_cv_local_module._PdflatexRunResult(returncode=1, page_count=2),
            compile_cv_local_module._PdflatexRunResult(returncode=1, page_count=5),
        ]
    )

    first = adapter.run_pass(
        build_dir=tmp_path, build_name="cover", cv_data_dir=tmp_path
    )
    second = adapter.run_pass(
        build_dir=tmp_path, build_name="cover", cv_data_dir=tmp_path
    )

    assert first.page_count == 2
    assert second.page_count == 5


def test_fake_pdflatex_adapter_page_count_none_when_not_configured(
    tmp_path: Path,
) -> None:
    adapter = compile_cv_local_module._CompileCvFakePdflatexAdapter(
        outcomes=[compile_cv_local_module._PdflatexRunResult(returncode=1)]
    )

    result = adapter.run_pass(
        build_dir=tmp_path,
        build_name="cover",
        cv_data_dir=tmp_path,
    )

    assert result.page_count is None
