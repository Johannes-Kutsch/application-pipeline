from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from application_pipeline.config import ConfigError
from application_pipeline.dedup import DedupStoreError
from application_pipeline.llm import ExtractorUnreachableError
from application_pipeline.orchestrator import RunSummary, run
from application_pipeline.prompts import PromptError
from application_pipeline.results import ResultsFileError


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write_config(
    tmp_path: Path,
    *,
    sources: str = '[SourceEntry(parser_type="bundesagentur_api")]',
    seen_store_path: str | None = None,
    with_prompt_files: bool = True,
) -> Path:
    """Write a minimal valid config.py and a prompts dir into tmp_path."""
    seen_line = (
        f"SEEN_STORE_PATH = {seen_store_path!r}" if seen_store_path is not None else ""
    )
    config_path = tmp_path / "config.py"
    config_path.write_text(
        textwrap.dedent(f"""
            from application_pipeline import SourceEntry
            KEYWORDS = ["python"]
            SKILLS = ["django"]
            SOURCES = {sources}
            LOCATIONS = ["Hamburg"]
            {seen_line}
        """),
        encoding="utf-8",
    )
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    if with_prompt_files:
        for name in ("classify_relevance.de.md", "classify_relevance.en.md"):
            (prompts_dir / name).write_text(
                "{title} {raw_description}", encoding="utf-8"
            )
        for name in ("judge_match.de.md", "judge_match.en.md"):
            (prompts_dir / name).write_text(
                "{skills} {raw_description}", encoding="utf-8"
            )
    return config_path


def _stub_extractor() -> MagicMock:
    ext = MagicMock()
    ext.prewarm.return_value = None
    return ext


def _stub_results_manager() -> MagicMock:
    rm = MagicMock()
    rm.ensure_initialized.return_value = None
    return rm


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_zero_summary_on_empty_run(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    summary = run(
        config_path,
        extractor=_stub_extractor(),
        dedup_store=MagicMock(),
        results_manager=_stub_results_manager(),
    )

    assert isinstance(summary, RunSummary)
    assert summary.total_discovered == 0
    assert summary.total_seen == 0
    assert summary.total_kept == 0
    assert summary.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# Fatal error paths
# ---------------------------------------------------------------------------


def test_config_error_propagates(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        run(tmp_path / "nonexistent.py")


def test_prompt_error_propagates(tmp_path: Path) -> None:
    # prompts dir exists but contains no prompt files → PromptError on load
    config_path = _write_config(tmp_path, with_prompt_files=False)

    with pytest.raises(PromptError):
        run(
            config_path,
            # extractor=None so load_prompts() is called
            dedup_store=MagicMock(),
            results_manager=_stub_results_manager(),
        )


def test_extractor_unreachable_propagates(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    failing = MagicMock()
    failing.prewarm.side_effect = ExtractorUnreachableError("ollama is down")

    with pytest.raises(ExtractorUnreachableError):
        run(
            config_path,
            extractor=failing,
            dedup_store=MagicMock(),
            results_manager=_stub_results_manager(),
        )


def test_prewarm_failure_no_parsers_instantiated(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    constructed: list[object] = []

    class TrackingParser:
        def __init__(self) -> None:
            constructed.append(self)

        def __enter__(self) -> "TrackingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    failing = MagicMock()
    failing.prewarm.side_effect = ExtractorUnreachableError("down")

    from application_pipeline.parsers import Parser

    def _registry(_: str) -> type[Parser] | None:
        return TrackingParser  # type: ignore[return-value]

    with pytest.raises(ExtractorUnreachableError):
        run(
            config_path,
            extractor=failing,
            parser_registry=_registry,
            dedup_store=MagicMock(),
            results_manager=_stub_results_manager(),
        )

    assert constructed == [], "parsers must not be instantiated before prewarm succeeds"


def test_dedup_store_error_propagates(tmp_path: Path) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("not-valid-json", encoding="utf-8")
    config_path = _write_config(tmp_path, seen_store_path=str(bad_json))

    with pytest.raises(DedupStoreError):
        run(
            config_path,
            extractor=_stub_extractor(),
            # dedup_store=None so the store is loaded from seen_store_path
            results_manager=_stub_results_manager(),
        )


def test_results_file_error_propagates(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    rm = MagicMock()
    rm.ensure_initialized.side_effect = ResultsFileError("cannot write")

    with pytest.raises(ResultsFileError):
        run(
            config_path,
            extractor=_stub_extractor(),
            dedup_store=MagicMock(),
            results_manager=rm,
        )


# ---------------------------------------------------------------------------
# Unknown parser_type → WARNING + excluded, run continues
# ---------------------------------------------------------------------------


def test_unknown_parser_type_run_continues(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path, sources='[SourceEntry(parser_type="no_such_parser")]'
    )

    summary = run(
        config_path,
        extractor=_stub_extractor(),
        dedup_store=MagicMock(),
        results_manager=_stub_results_manager(),
    )

    assert isinstance(summary, RunSummary)
    assert summary.total_discovered == 0
