from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from application_pipeline import dedup as dedup_module
from application_pipeline.config import ConfigError
from application_pipeline.dedup import DedupStoreError
from application_pipeline.llm import ExtractorUnreachableError
from application_pipeline.orchestrator import RunSummary, run
from application_pipeline.parsers import ParserQuery, Position, PositionStub
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
    keywords: str = '["python"]',
    locations: str = '["Hamburg"]',
    include_remote: bool = True,
    negative_keywords: str = "[]",
) -> Path:
    """Write a minimal valid config.py and a prompts dir into tmp_path."""
    seen_line = (
        f"SEEN_STORE_PATH = {seen_store_path!r}" if seen_store_path is not None else ""
    )
    config_path = tmp_path / "config.py"
    config_path.write_text(
        textwrap.dedent(f"""
            from application_pipeline import SourceEntry
            KEYWORDS = {keywords}
            SKILLS = ["django"]
            SOURCES = {sources}
            LOCATIONS = {locations}
            INCLUDE_REMOTE = {include_remote!r}
            NEGATIVE_KEYWORDS = {negative_keywords}
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
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
        results_manager=_stub_results_manager(),
    )

    assert isinstance(summary, RunSummary)
    assert summary.total_discovered == 0
    assert summary.total_seen == 0
    assert summary.total_kept == 0
    assert summary.duration_seconds >= 0.0
    assert summary.discovered == 0
    assert summary.skipped == 0
    assert summary.enriched == ()


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


# ---------------------------------------------------------------------------
# Integration: discover + dedup gating + enrich (no LLM)
# ---------------------------------------------------------------------------

_STUB_URLS = [f"https://stub.example/{i}" for i in range(6)]


class _StubParser:
    """Returns 3 stubs per discover() call (deterministic URLs), enriches trivially."""

    def __init__(self) -> None:
        self._call = 0

    def __enter__(self) -> "_StubParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        base = self._call * 3
        self._call += 1
        return [
            PositionStub(
                url=_STUB_URLS[base + i], title=f"Job {base + i}", source="stub"
            )
            for i in range(3)
        ]

    def enrich(self, stub: PositionStub) -> Position:
        return Position(stub=stub, raw_description="test description")


def test_integration_discover_and_enrich(tmp_path: Path) -> None:
    """2 keywords × 1 location, 3 stubs each → discovered==6, skipped==0, enriched==6."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="stub")]',
        keywords='["python", "django"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    summary = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    assert summary.discovered == 6
    assert summary.skipped == 0
    assert len(summary.enriched) == 6
    assert all(isinstance(p, Position) for p, _lang in summary.enriched)


def test_integration_all_skipped_when_preseeded(tmp_path: Path) -> None:
    """Pre-seed all 6 URLs → discovered==6, skipped==6, enriched empty."""
    seen_path = tmp_path / ".seen.json"
    seen_data = {
        url: {
            "company_lc": None,
            "title_lc": None,
            "location_lc": None,
            "status": "off_domain",
            "first_seen": "2024-01-01",
        }
        for url in _STUB_URLS
    }
    seen_path.write_text(json.dumps(seen_data), encoding="utf-8")

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="stub")]',
        keywords='["python", "django"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    summary = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=_stub_results_manager(),
    )

    assert summary.discovered == 6
    assert summary.skipped == 6
    assert summary.enriched == ()


def test_integration_include_remote_emits_extra_discover_calls(tmp_path: Path) -> None:
    """include_remote=True adds one (keyword, None) call per keyword per source."""
    queries_received: list[ParserQuery] = []

    class _TrackingParser:
        def __enter__(self) -> "_TrackingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            queries_received.append(query)
            return []

        def enrich(self, stub: PositionStub) -> Position:  # pragma: no cover
            raise NotImplementedError

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="stub")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=True,
    )

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _TrackingParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    assert len(queries_received) == 2
    geo_calls = [q for q in queries_received if q.location is not None]
    remote_calls = [q for q in queries_received if q.location is None]
    assert len(geo_calls) == 1
    assert len(remote_calls) == 1
    assert geo_calls[0].location == "Hamburg"


# ---------------------------------------------------------------------------
# Integration: language resolution + Pre-Filter pass (slice 4c)
# ---------------------------------------------------------------------------

_STUB_URLS_PF = [f"https://stub.example/pf/{i}" for i in range(6)]
_REJECTED_URL = _STUB_URLS_PF[2]


class _PreFilterStubParser:
    """6 stubs; stub at index 2 gets 'excluded' in its description, others don't."""

    def __enter__(self) -> "_PreFilterStubParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return [
            PositionStub(
                url=_STUB_URLS_PF[i], title=f"Job {i}", source="stub", language="en"
            )
            for i in range(6)
        ]

    def enrich(self, stub: PositionStub) -> Position:
        desc = (
            "excluded position" if stub.url == _REJECTED_URL else "regular job listing"
        )
        return Position(stub=stub, raw_description=desc)


def test_integration_prefilter_rejects_off_domain(tmp_path: Path) -> None:
    """1 of 6 positions fails Pre-Filter → prefilter_dropped==1, URL in .seen.json as off_domain, 5 survivors."""
    seen_path = tmp_path / ".seen.json"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="stub")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )

    summary = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _PreFilterStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=_stub_results_manager(),
    )

    assert summary.discovered == 6
    assert summary.skipped == 0
    assert summary.prefilter_dropped == 1
    assert len(summary.enriched) == 5

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_REJECTED_URL]["status"] == "off_domain"
    surviving_urls = {p.stub.url for p, _lang in summary.enriched}
    assert _REJECTED_URL not in surviving_urls
