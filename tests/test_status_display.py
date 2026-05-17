from __future__ import annotations

import logging
import threading
import unittest.mock
from pathlib import Path

import pytest

import application_pipeline.parser_log as _parser_log
from application_pipeline.status_display import (
    PlainStatusDisplay,
    RichStatusDisplay,
    _LiveLoggingHandler,
)
from fake_status_display import FakeStatusDisplay


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


def test_plain_register_writes_to_lifecycle_jsonl(tmp_path: Path) -> None:
    import json

    _parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")

    rows = [
        json.loads(line)
        for line in (tmp_path / "lifecycle.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        r["event"] == "registered" and r["component"] == "pipeline" and r["order"] == 0
        for r in rows
    )


def test_plain_update_phase_transition_writes_to_lifecycle_jsonl(
    tmp_path: Path,
) -> None:
    import json

    _parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")
    display.update_phase("pipeline", phase="done")

    rows = [
        json.loads(line)
        for line in (tmp_path / "lifecycle.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(r["event"] == "phase_changed" and r["phase"] == "done" for r in rows)


def test_plain_update_phase_no_transition_does_not_write_extra_lifecycle_row(
    tmp_path: Path,
) -> None:
    _parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")

    lines_before = (
        (tmp_path / "lifecycle.jsonl").read_text(encoding="utf-8").splitlines()
    )
    display.update_phase("pipeline", phase="running")
    lines_after = (
        (tmp_path / "lifecycle.jsonl").read_text(encoding="utf-8").splitlines()
    )

    assert len(lines_before) == len(lines_after)


def test_plain_remove_writes_to_lifecycle_jsonl(tmp_path: Path) -> None:
    import json

    _parser_log.configure(tmp_path)
    display = PlainStatusDisplay()
    display.register("pipeline", order=0, phase="running")
    display.remove("pipeline")

    rows = [
        json.loads(line)
        for line in (tmp_path / "lifecycle.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(r["event"] == "removed" and r["component"] == "pipeline" for r in rows)


# ---------------------------------------------------------------------------
# Logging shim
# ---------------------------------------------------------------------------


def test_plain_does_not_install_log_handler() -> None:
    root = logging.getLogger()
    count_before = len(root.handlers)
    display = PlainStatusDisplay()
    display.stop()
    assert len(root.handlers) == count_before


def test_log_warning_forwarded_to_display_print_during_active_session() -> None:
    fake = FakeStatusDisplay()
    handler = _LiveLoggingHandler(fake)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        logging.getLogger("application_pipeline.orchestrator").warning(
            "test warning message"
        )
        print_calls = [c for c in fake.calls if c.method == "print"]
        assert len(print_calls) == 1
        assert "test warning message" in str(print_calls[0].kwargs["message"])
        assert print_calls[0].name == "application_pipeline.orchestrator"
    finally:
        root.removeHandler(handler)


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_plain_concurrent_phase_updates_produce_complete_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    display = PlainStatusDisplay()
    n = 20
    for i in range(n):
        display.register(f"parser-{i}", order=i, phase="starting")
    capsys.readouterr()

    barrier = threading.Barrier(n)

    def flip(name: str) -> None:
        barrier.wait()
        display.update_phase(name, phase="done")

    threads = [threading.Thread(target=flip, args=(f"parser-{i}",)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) == n
    for line in lines:
        assert line.endswith("phase=done")


@pytest.fixture
def rich_display():
    with unittest.mock.patch("rich.live.Live") as mock_live_cls:
        mock_live_cls.return_value.console = unittest.mock.MagicMock()
        display = RichStatusDisplay()
        yield display
        display.stop()


def test_rich_survives_concurrent_access(rich_display: RichStatusDisplay) -> None:
    n = 20
    for i in range(n):
        rich_display.register(f"parser-{i}", order=i, phase="starting")

    barrier = threading.Barrier(n)
    errors: list[Exception] = []

    def worker(name: str) -> None:
        try:
            barrier.wait()
            rich_display.update_phase(name, phase="running")
            rich_display.update_body(name, body="discovered=1")
            rich_display.update_phase(name, phase="done")
            rich_display.remove(name)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"parser-{i}",)) for i in range(n)]
    # also run register calls from extra threads to stress concurrent mutation
    threads += [
        threading.Thread(
            target=rich_display.update_body,
            args=(f"parser-{i % n}",),
            kwargs={"body": "stress"},
        )
        for i in range(n * 2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
