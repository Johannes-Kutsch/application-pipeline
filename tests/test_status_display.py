from __future__ import annotations

from pathlib import Path

import pytest

import application_pipeline.parser_log as _parser_log
from application_pipeline.status_display import PlainStatusDisplay


@pytest.fixture(autouse=True)
def _reset_log_state():
    _parser_log._logs_dir = None
    yield
    _parser_log._logs_dir = None


# ---------------------------------------------------------------------------
# Exact line output
# ---------------------------------------------------------------------------


def test_plain_register_prints_line(capsys: pytest.CaptureFixture[str]) -> None:
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")

    out = capsys.readouterr().out
    assert out == "pipeline: registered order=0 phase=running\n"


def test_plain_update_phase_on_transition_prints_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")
    capsys.readouterr()  # flush register output

    display.update_phase("pipeline", phase="done")

    out = capsys.readouterr().out
    assert out == "pipeline: phase=done\n"


def test_plain_update_phase_no_transition_is_silent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")
    capsys.readouterr()

    display.update_phase("pipeline", phase="running")

    out = capsys.readouterr().out
    assert out == ""


def test_plain_update_body_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")
    capsys.readouterr()

    display.update_body("pipeline", body="discovered=5 written=0 errors=0")

    out = capsys.readouterr().out
    assert out == ""


def test_plain_remove_prints_line(capsys: pytest.CaptureFixture[str]) -> None:
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")
    capsys.readouterr()

    display.remove("pipeline")

    out = capsys.readouterr().out
    assert out == "pipeline: removed\n"


def test_plain_full_sequence_exact_output(capsys: pytest.CaptureFixture[str]) -> None:
    display = PlainStatusDisplay()

    display.register("pipeline", order=0, phase="running")
    display.update_body("pipeline", body="discovered=5 written=0 errors=0")  # silent
    display.update_phase("pipeline", phase="running")  # same phase — no output
    display.update_phase("pipeline", phase="done")  # transition — prints
    display.remove("pipeline")

    out = capsys.readouterr().out
    assert out == (
        "pipeline: registered order=0 phase=running\n"
        "pipeline: phase=done\n"
        "pipeline: removed\n"
    )


def test_plain_stop_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    display = PlainStatusDisplay()
    display.stop()
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# parser_log integration
# ---------------------------------------------------------------------------


def test_plain_register_writes_to_parser_log(tmp_path: Path) -> None:
    _parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")

    log_content = (tmp_path / "pipeline.log").read_text(encoding="utf-8")
    assert "registered" in log_content
    assert "order=0" in log_content
    assert "phase=running" in log_content


def test_plain_update_phase_transition_writes_to_parser_log(tmp_path: Path) -> None:
    _parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")
    display.update_phase("pipeline", phase="done")

    log_content = (tmp_path / "pipeline.log").read_text(encoding="utf-8")
    assert "phase_changed" in log_content
    assert "phase=done" in log_content


def test_plain_update_phase_no_transition_does_not_write_parser_log(
    tmp_path: Path,
) -> None:
    _parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")

    log_before = (tmp_path / "pipeline.log").read_text(encoding="utf-8")
    display.update_phase("pipeline", phase="running")
    log_after = (tmp_path / "pipeline.log").read_text(encoding="utf-8")

    assert log_before == log_after


def test_plain_remove_writes_to_parser_log(tmp_path: Path) -> None:
    _parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")
    display.remove("pipeline")

    log_content = (tmp_path / "pipeline.log").read_text(encoding="utf-8")
    assert "removed" in log_content
