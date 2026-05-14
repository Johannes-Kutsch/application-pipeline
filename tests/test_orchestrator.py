from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import application_pipeline.parser_log as _parser_log

from fake_status_display import FakeStatusDisplay

from application_pipeline import dedup as dedup_module
from application_pipeline.config import ConfigError
from application_pipeline.dedup import DedupStoreError
from application_pipeline.llm import (
    CallUsage,
    ClassifyItem,
    ExtractorBatchMalformedError,
    ExtractorError,
    ExtractorUnreachableError,
    MatchTier,
    MatchVerdict,
    RelevanceVerdict,
)
from application_pipeline.llm.claude_cli import ClaudeUsageLimitError
from application_pipeline.orchestrator import RunSummary, run
from application_pipeline.parsers import (
    ExternalRedirect,
    Parser,
    ParserQuery,
    Position,
    PositionStub,
)
from application_pipeline.parsers.types import City, Remote
from application_pipeline.parsers.errors import ParserError
from application_pipeline.prompts import PromptError
from application_pipeline.results import ResultsFileError, ResultsFileManager


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_log_state():
    """Reset parser_log module state between tests.

    Some tests call main() which configures parser_log. Without this reset
    the configured logs path leaks across tests.
    """
    _parser_log._logs_dir = None
    yield
    _parser_log._logs_dir = None


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
            (prompts_dir / name).write_text("{ITEMS}", encoding="utf-8")
        for name in ("judge_match.de.md", "judge_match.en.md"):
            (prompts_dir / name).write_text(
                "{skills} {raw_description}", encoding="utf-8"
            )
    return config_path


_ZERO_USAGE = CallUsage(
    input_tokens=0, output_tokens=0, cache_read_tokens=0, cost_usd=0.0, duration_s=0.0
)


def _stub_extractor() -> MagicMock:
    ext = MagicMock()
    ext.prewarm.return_value = None
    ext.classify_relevance_batch.side_effect = lambda lang, items: (
        [RelevanceVerdict(in_domain=True) for _ in items],
        _ZERO_USAGE,
    )
    ext.judge_match.return_value = (
        MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok"),
        _ZERO_USAGE,
    )
    return ext


def _stub_results_manager() -> MagicMock:
    rm = MagicMock()
    rm.ensure_initialized.return_value = None
    rm.next_position_number.return_value = 1
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
    assert summary.discovered == 0
    assert summary.skipped == 0
    assert summary.written == 0
    assert summary.classifier_dropped == 0
    assert summary.prefilter_dropped == 0
    assert summary.green == 0
    assert summary.amber == 0
    assert summary.red == 0
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
    config_path = _write_config(tmp_path)

    summary = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
        results_manager=_stub_results_manager(),
    )

    assert isinstance(summary, RunSummary)
    assert summary.discovered == 0


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
    """2 keywords × 1 location, 3 stubs each → discovered==6, skipped==0, written==6."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
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
    assert summary.written == 6


def test_integration_all_skipped_when_preseeded(tmp_path: Path) -> None:
    """Pre-seed all 6 URLs → discovered==6, skipped==6, written==0."""
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
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
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
    assert summary.written == 0


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
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
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
    geo_calls = [q for q in queries_received if isinstance(q.location, City)]
    remote_calls = [q for q in queries_received if isinstance(q.location, Remote)]
    assert len(geo_calls) == 1
    assert len(remote_calls) == 1
    assert geo_calls[0].location == City("Hamburg")


# ---------------------------------------------------------------------------
# Integration: discover short-circuit on consecutive url_hits (issue #111)
# ---------------------------------------------------------------------------


def test_discover_short_circuits_after_n_consecutive_url_hits(tmp_path: Path) -> None:
    """3 misses + 60 url_hits → short-circuit after 50th url_hit; 53 stubs consumed."""
    consumed: list[int] = [0]

    all_stubs = [
        PositionStub(url=f"https://sc.example/{i}", title=f"Job {i}", source="stub")
        for i in range(63)
    ]

    class _GenParser:
        def __enter__(self) -> "_GenParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            for stub in all_stubs:
                consumed[0] += 1
                yield stub

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="test")

    dedup = MagicMock()
    dedup.is_seen.side_effect = ["miss"] * 3 + ["url_hit"] * 60

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _GenParser,  # type: ignore[return-value]
        dedup_store=dedup,
        results_manager=_stub_results_manager(),
    )

    assert consumed[0] == 53  # 3 misses + 50 url_hits before close
    assert summary.discovered == 53
    assert summary.skipped == 50


def test_discover_counter_resets_on_miss(tmp_path: Path) -> None:
    """3 url_hits, 1 miss, 49 url_hits → counter resets at miss; all 53 consumed without short-circuit."""
    consumed: list[int] = [0]

    all_stubs = [
        PositionStub(url=f"https://sc.example/{i}", title=f"Job {i}", source="stub")
        for i in range(53)
    ]

    class _GenParser:
        def __enter__(self) -> "_GenParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            for stub in all_stubs:
                consumed[0] += 1
                yield stub

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="test")

    dedup = MagicMock()
    dedup.is_seen.side_effect = ["url_hit"] * 3 + ["miss"] + ["url_hit"] * 49

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _GenParser,  # type: ignore[return-value]
        dedup_store=dedup,
        results_manager=_stub_results_manager(),
    )

    assert consumed[0] == 53  # all consumed — miss reset the counter after 3
    assert summary.discovered == 53
    assert summary.skipped == 52  # 3 + 49 url_hits


def test_discover_tuple_hit_resets_url_hit_counter(tmp_path: Path) -> None:
    """tuple_hits interspersed in url_hits reset the counter; no false short-circuit."""
    consumed: list[int] = [0]

    # 25 url_hits, tuple_hit (resets), 25 url_hits, tuple_hit (resets), 25 url_hits = 77
    all_stubs = [
        PositionStub(url=f"https://sc.example/{i}", title=f"Job {i}", source="stub")
        for i in range(77)
    ]

    class _GenParser:
        def __enter__(self) -> "_GenParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            for stub in all_stubs:
                consumed[0] += 1
                yield stub

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="test")

    dedup = MagicMock()
    dedup.is_seen.side_effect = (
        ["url_hit"] * 25
        + ["tuple_hit"]
        + ["url_hit"] * 25
        + ["tuple_hit"]
        + ["url_hit"] * 25
    )

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _GenParser,  # type: ignore[return-value]
        dedup_store=dedup,
        results_manager=_stub_results_manager(),
    )

    assert (
        consumed[0] == 77
    )  # all consumed — tuple_hits reset counter, never reached 50 consecutive
    assert summary.discovered == 77
    assert summary.skipped == 77


# ---------------------------------------------------------------------------
# Integration: dedup counter breakdown (issue #177)
# ---------------------------------------------------------------------------


def test_integration_dedup_counter_breakdown(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """2 url_hits + 1 tuple_hit + 4 misses → dedup_url_hits=2 dedup_tuple_hits=1 dedup_misses=4 in RunSummary and run complete: log."""
    import logging

    _DEDUP_URLS = [f"https://dedup.example/{i}" for i in range(7)]

    class _SevenStubParser:
        def __enter__(self) -> "_SevenStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=_DEDUP_URLS[i], title=f"Job {i}", source="stub", language="en"
                )
                for i in range(7)
            ]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="good description")

    dedup = MagicMock()
    # 4 misses, 2 url_hits, 1 tuple_hit
    dedup.is_seen.side_effect = [
        "miss",
        "miss",
        "url_hit",
        "miss",
        "tuple_hit",
        "url_hit",
        "miss",
    ]

    with caplog.at_level(logging.INFO, logger="application_pipeline.orchestrator"):
        summary = run(
            _write_config(
                tmp_path,
                sources='[SourceEntry(parser_type="bundesagentur_api")]',
                keywords='["python"]',
                locations='["Hamburg"]',
                include_remote=False,
            ),
            extractor=_stub_extractor(),
            parser_registry=lambda _: _SevenStubParser,  # type: ignore[return-value]
            dedup_store=dedup,
            results_manager=_stub_results_manager(),
        )

    assert summary.dedup_url_hits == 2
    assert summary.dedup_tuple_hits == 1
    assert summary.dedup_misses == 4
    assert summary.skipped == summary.dedup_url_hits + summary.dedup_tuple_hits

    run_complete = next(
        r.getMessage() for r in caplog.records if "run complete:" in r.getMessage()
    )
    assert "dedup_url_hits=2" in run_complete
    assert "dedup_tuple_hits=1" in run_complete
    assert "dedup_misses=4" in run_complete


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
    """1 of 6 positions fails Pre-Filter → prefilter_dropped==1, URL in .seen.json as off_domain, 5 written."""
    seen_path = tmp_path / ".seen.json"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
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
    assert summary.prefilter_considered == 6
    assert summary.prefilter_passed == 5
    assert summary.prefilter_dropped == 1
    assert summary.prefilter_blacklist_hits == 1
    assert summary.prefilter_whitelist_hits == 0
    assert summary.prefilter_no_hit_either == 5
    assert summary.written == 5

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_REJECTED_URL]["status"] == "off_domain"


# ---------------------------------------------------------------------------
# Integration: prefilter whitelist-rescue counter (issue #176)
# ---------------------------------------------------------------------------

_STUB_URLS_WL = [f"https://stub.example/wl/{i}" for i in range(3)]
_WL_BLACKLIST_ONLY_URL = _STUB_URLS_WL[0]  # blacklist hit, no whitelist → drops
_WL_RESCUE_URL = _STUB_URLS_WL[1]  # blacklist + whitelist hit → passes (rescue)
_WL_NO_HIT_URL = _STUB_URLS_WL[2]  # no hit either → passes


class _WhitelistRescueStubParser:
    """3 stubs exercising all three prefilter verdict categories."""

    def __enter__(self) -> "_WhitelistRescueStubParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return [
            PositionStub(url=url, title=f"Job {i}", source="stub", language="en")
            for i, url in enumerate(_STUB_URLS_WL)
        ]

    def enrich(self, stub: PositionStub) -> Position:
        if stub.url == _WL_BLACKLIST_ONLY_URL:
            desc = "Pflegekraft gesucht"
        elif stub.url == _WL_RESCUE_URL:
            desc = "Pflegekraft mit Django-Kenntnissen gesucht"
        else:
            desc = "Marketing Manager position"
        return Position(stub=stub, raw_description=desc)


def test_integration_prefilter_whitelist_rescue_counters(tmp_path: Path) -> None:
    """Whitelist-rescue, blacklist-only-drop, and no-hit-either are counted correctly."""
    seen_path = tmp_path / ".seen.json"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["pflegekraft"]',
    )

    summary = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _WhitelistRescueStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=_stub_results_manager(),
    )

    assert summary.prefilter_considered == 3
    assert summary.prefilter_dropped == 1
    assert summary.prefilter_passed == 2
    assert summary.prefilter_blacklist_hits == 2  # blacklist-only + rescue
    assert summary.prefilter_whitelist_hits == 1  # only rescue position
    assert summary.prefilter_no_hit_either == 1  # marketing manager


# ---------------------------------------------------------------------------
# Integration: classify + judge + render + write + mark (slice 5 / issue #109)
# ---------------------------------------------------------------------------

_STUB_URLS_LLM = [f"https://stub.example/llm/{i}" for i in range(6)]
_PF_REJECTED_LLM_URL = _STUB_URLS_LLM[0]  # prefilter rejects: "excluded" in description
_CLS_REJECTED_LLM_URL = _STUB_URLS_LLM[1]  # classifier rejects: title "Job 1"
# URLs 2-5 are judged with tiers: green, amber, red, amber
_LLM_JUDGE_TIERS = {
    _STUB_URLS_LLM[2]: MatchTier.green,
    _STUB_URLS_LLM[3]: MatchTier.amber,
    _STUB_URLS_LLM[4]: MatchTier.red,
    _STUB_URLS_LLM[5]: MatchTier.amber,
}


class _LLMStubParser:
    """6 stubs with distinct descriptions for prefilter/classifier/judge discrimination."""

    def __enter__(self) -> "_LLMStubParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return [
            PositionStub(
                url=_STUB_URLS_LLM[i],
                title=f"Job {i}",
                source="stub",
                language="en",
            )
            for i in range(6)
        ]

    def enrich(self, stub: PositionStub) -> Position:
        if stub.url == _PF_REJECTED_LLM_URL:
            desc = "excluded position"
        else:
            # Encode URL index in description so judge can return the right tier
            idx = _STUB_URLS_LLM.index(stub.url)
            desc = f"description for job {idx}"
        return Position(stub=stub, raw_description=desc)


_FAKE_CLASSIFY_USAGE = CallUsage(
    input_tokens=10,
    output_tokens=5,
    cache_read_tokens=2,
    cost_usd=0.001,
    duration_s=0.5,
)
_FAKE_JUDGE_USAGE = CallUsage(
    input_tokens=8,
    output_tokens=4,
    cache_read_tokens=1,
    cost_usd=0.0008,
    duration_s=0.4,
)


class _FakeExtractor:
    """Deterministic extractor: rejects Job 1 at classify, returns fixed tiers at judge."""

    def prewarm(self) -> None:
        pass

    def classify_relevance_batch(
        self, language: str, items: list[ClassifyItem]
    ) -> tuple[list[RelevanceVerdict], CallUsage]:
        verdicts = [
            RelevanceVerdict(in_domain=(item.title != "Job 1")) for item in items
        ]
        return verdicts, _FAKE_CLASSIFY_USAGE

    def judge_match(
        self, language: str, raw_description: str
    ) -> tuple[MatchVerdict, CallUsage]:
        # Extract job index from description ("description for job N")
        for idx, url in enumerate(_STUB_URLS_LLM):
            if f"job {idx}" in raw_description:
                tier = _LLM_JUDGE_TIERS.get(url, MatchTier.green)
                return MatchVerdict(
                    tier=tier, matched=[], missing=[], summary="ok"
                ), _FAKE_JUDGE_USAGE
        return MatchVerdict(
            tier=MatchTier.green, matched=[], missing=[], summary="ok"
        ), _FAKE_JUDGE_USAGE


def test_integration_classify_judge_render_write_mark(tmp_path: Path) -> None:
    """Happy path: 6 stubs, 1 prefilter-dropped, 1 classifier-dropped, 4 written."""
    seen_path = tmp_path / ".seen.json"
    results_path = tmp_path / "current.md"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )

    summary = run(
        config_path,
        extractor=_FakeExtractor(),
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=ResultsFileManager(results_path, "# Results\n\n"),
    )

    assert summary.discovered == 6
    assert summary.skipped == 0
    assert summary.prefilter_dropped == 1
    assert summary.classifier_dropped == 1
    assert summary.written == 4
    assert summary.green == 1
    assert summary.amber == 2
    assert summary.red == 1

    # .seen.json: 2 off_domain, 4 kept
    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    off_domain = [
        url for url, rec in seen_data.items() if rec["status"] == "off_domain"
    ]
    kept = [url for url, rec in seen_data.items() if rec["status"] == "kept"]
    assert len(off_domain) == 2
    assert len(kept) == 4
    assert _PF_REJECTED_LLM_URL in off_domain
    assert _CLS_REJECTED_LLM_URL in off_domain

    # current.md: 4 numbered entries
    content = results_path.read_text(encoding="utf-8")
    numbers = re.findall(r"^## (\d+)\.", content, re.MULTILINE)
    assert len(numbers) == 4


def test_integration_dedup_skip_rerun(tmp_path: Path) -> None:
    """Second run on same tmp_path → all 6 skipped, current.md unchanged."""
    seen_path = tmp_path / ".seen.json"
    results_path = tmp_path / "current.md"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )

    def _make_run() -> RunSummary:
        return run(
            config_path,
            extractor=_FakeExtractor(),
            parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(seen_path),
            results_manager=ResultsFileManager(results_path, "# Results\n\n"),
        )

    first = _make_run()
    assert first.written == 4

    numbers_after_first = re.findall(
        r"^## (\d+)\.", results_path.read_text(encoding="utf-8"), re.MULTILINE
    )

    second = _make_run()
    assert second.discovered == 6
    assert second.skipped == 6
    assert second.prefilter_dropped == 0
    assert second.classifier_dropped == 0
    assert second.written == 0
    assert second.green == 0
    assert second.amber == 0
    assert second.red == 0

    # No new position entries added; second run only appends its own Run Divider
    numbers_after_second = re.findall(
        r"^## (\d+)\.", results_path.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert numbers_after_second == numbers_after_first


def test_classify_batch_precedes_judge_batch(tmp_path: Path) -> None:
    """All classify_relevance_batch calls complete before any judge_match call."""
    call_log: list[str] = []

    class _InstrumentedExtractor:
        def prewarm(self) -> None:
            pass

        def classify_relevance_batch(
            self, language: str, items: list[ClassifyItem]
        ) -> tuple[list[RelevanceVerdict], CallUsage]:
            call_log.extend(["classify"] * len(items))
            return [RelevanceVerdict(in_domain=True) for _ in items], _ZERO_USAGE

        def judge_match(
            self, language: str, raw_description: str
        ) -> tuple[MatchVerdict, CallUsage]:
            call_log.append("judge")
            return (
                MatchVerdict(
                    tier=MatchTier.green, matched=[], missing=[], summary="ok"
                ),
                _ZERO_USAGE,
            )

    class _MultiStubParser:
        """Emits 5 stubs, all pass prefilter."""

        def __enter__(self) -> "_MultiStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=f"https://batch.example/{i}",
                    title=f"Job {i}",
                    source="stub",
                    language="en",
                )
                for i in range(5)
            ]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="good job description")

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    run(
        config_path,
        extractor=_InstrumentedExtractor(),
        parser_registry=lambda _: _MultiStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    assert call_log.count("classify") == 5
    assert call_log.count("judge") == 5
    # All classify calls before any judge call
    last_classify = max(i for i, c in enumerate(call_log) if c == "classify")
    first_judge = min(i for i, c in enumerate(call_log) if c == "judge")
    assert last_classify < first_judge, (
        f"classify and judge calls interleaved: {call_log}"
    )


# ---------------------------------------------------------------------------
# Error paths (issue #110)
# ---------------------------------------------------------------------------

_ERR_URLS = [f"https://stub.example/err/{i}" for i in range(4)]


class _TwoStubParser:
    """Yields 2 stubs with fixed URLs; enriches trivially."""

    def __enter__(self) -> "_TwoStubParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return [
            PositionStub(
                url=_ERR_URLS[i], title=f"Job {i}", source="stub", language="en"
            )
            for i in range(2)
        ]

    def enrich(self, stub: PositionStub) -> Position:
        return Position(stub=stub, raw_description="good description")


def _two_stub_config(tmp_path: Path) -> Path:
    return _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )


def test_extractor_error_on_classify_leaves_positions_unseen(tmp_path: Path) -> None:
    """ExtractorError on classify_relevance_batch: NO position in batch marked seen, run continues with next batch."""
    seen_path = tmp_path / ".seen.json"

    call_count = [0]

    def _batch_side_effect(
        lang: str, items: list[ClassifyItem]
    ) -> tuple[list[RelevanceVerdict], CallUsage]:
        call_count[0] += 1
        if call_count[0] == 1:
            raise ExtractorError("classify batch boom")
        return [RelevanceVerdict(in_domain=True) for _ in items], _ZERO_USAGE

    ext = MagicMock()
    ext.prewarm.return_value = None
    ext.classify_relevance_batch.side_effect = _batch_side_effect
    ext.judge_match.return_value = (
        MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok"),
        _ZERO_USAGE,
    )

    # Use batch_size=1 so each position is its own batch
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    # Write CLAUDE_CLASSIFY_BATCH_SIZE=1 into config
    config_text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config_text + "\nCLAUDE_CLASSIFY_BATCH_SIZE = 1\n", encoding="utf-8"
    )

    summary = run(
        config_path,
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=_stub_results_manager(),
    )

    # First batch errored (1 position), second batch succeeded (1 position written)
    assert summary.errored == 1
    assert summary.written == 1

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    # First position must NOT be in seen store (left un-seen for retry)
    assert _ERR_URLS[0] not in seen_data


def test_extractor_error_on_judge_leaves_position_unseen(tmp_path: Path) -> None:
    """ExtractorError on judge_match: position NOT marked seen, rendered block NOT in current.md, errored increments."""
    seen_path = tmp_path / ".seen.json"
    results_path = tmp_path / "current.md"

    ext = MagicMock()
    ext.prewarm.return_value = None
    ext.classify_relevance_batch.side_effect = lambda lang, items: (
        [RelevanceVerdict(in_domain=True) for _ in items],
        _ZERO_USAGE,
    )
    # First judge raises, second succeeds
    ext.judge_match.side_effect = [
        ExtractorError("judge boom"),
        (
            MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok"),
            _ZERO_USAGE,
        ),
    ]

    summary = run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=ResultsFileManager(results_path, "# Results\n\n"),
    )

    assert summary.errored == 1
    assert summary.written == 1

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert _ERR_URLS[0] not in seen_data

    content = results_path.read_text(encoding="utf-8")
    assert "Job 0" not in content


def test_parser_error_on_enrich_marks_enrich_failed(tmp_path: Path) -> None:
    """ParserError from enrich: stub marked enrich_failed, enrich_failed increments, other stubs proceed."""
    seen_path = tmp_path / ".seen.json"

    class _EnrichFailParser:
        def __enter__(self) -> "_EnrichFailParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=_ERR_URLS[i], title=f"Job {i}", source="stub", language="en"
                )
                for i in range(3)
            ]

        def enrich(self, stub: PositionStub) -> Position:
            if stub.url == _ERR_URLS[1]:
                raise ParserError("enrich boom")
            return Position(stub=stub, raw_description="good description")

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _EnrichFailParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=_stub_results_manager(),
    )

    assert summary.enrich_failed == 1
    assert summary.written == 2

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_ERR_URLS[1]]["status"] == "enrich_failed"


def test_external_redirect_marks_seen_and_increments_counter(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ExternalRedirect: stub marked external_redirect, external_redirects increments, enrich_failed unchanged, event in parser log, no WARNING."""
    import logging

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    seen_path = tmp_path / ".seen.json"

    class _RedirectParser:
        def __enter__(self) -> "_RedirectParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=_ERR_URLS[i], title=f"Job {i}", source="stub", language="en"
                )
                for i in range(2)
            ]

        def enrich(self, stub: PositionStub) -> Position | ExternalRedirect:
            if stub.url == _ERR_URLS[0]:
                return ExternalRedirect(
                    stub=stub, outbound_url="https://external.example/job"
                )
            return Position(stub=stub, raw_description="good description")

    with caplog.at_level(logging.WARNING, logger="application_pipeline.orchestrator"):
        summary = run(
            _write_config(
                tmp_path,
                sources='[SourceEntry(parser_type="bundesagentur_api")]',
                keywords='["python"]',
                locations='["Hamburg"]',
                include_remote=False,
            ),
            extractor=_stub_extractor(),
            parser_registry=lambda _: _RedirectParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(seen_path),
            results_manager=_stub_results_manager(),
        )

    assert summary.external_redirects == 1
    assert summary.enrich_failed == 0
    assert summary.written == 1

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_ERR_URLS[0]]["status"] == "external_redirect"

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records == [], f"unexpected WARNING(s): {warning_records}"

    log_content = (logs_dir / "bundesagentur_api.log").read_text(encoding="utf-8")
    assert "external_redirect" in log_content
    assert "https://external.example/job" in log_content


def test_parser_error_mid_discover_processes_yielded_stubs(tmp_path: Path) -> None:
    """ParserError mid-discover: already-yielded stubs processed, run advances to next combination."""
    seen_path = tmp_path / ".seen.json"

    class _MidDiscoverFailParser:
        def __enter__(self) -> "_MidDiscoverFailParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            # Yield 3 stubs then raise ParserError
            for i in range(3):
                yield PositionStub(
                    url=_ERR_URLS[i], title=f"Job {i}", source="stub", language="en"
                )
            raise ParserError("mid-discover boom")

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="good description")

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _MidDiscoverFailParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=_stub_results_manager(),
    )

    assert summary.discovered == 3
    assert summary.written == 3


# ---------------------------------------------------------------------------
# Threading: PARSER_DEAD (issue #112)
# ---------------------------------------------------------------------------


def test_parser_thread_dead_run_completes(tmp_path: Path) -> None:
    """Uncaught exception in parser thread → parsers_dead==1, run completes (no hang)."""

    class _DeadParser:
        def __enter__(self) -> "_DeadParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            raise RuntimeError("unexpected crash")
            yield  # pragma: no cover — makes this a generator

        def enrich(self, stub: PositionStub) -> Position:  # pragma: no cover
            raise NotImplementedError

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _DeadParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    assert summary.parsers_dead == 1
    assert summary.discovered == 0
    assert summary.written == 0


def test_parser_thread_dead_surviving_parsers_continue(tmp_path: Path) -> None:
    """One dead parser + one healthy parser → dead counted, healthy stubs written."""

    class _DeadParser:
        def __enter__(self) -> "_DeadParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            raise RuntimeError("boom")
            yield  # pragma: no cover

        def enrich(self, stub: PositionStub) -> Position:  # pragma: no cover
            raise NotImplementedError

    class _HealthyParser:
        def __enter__(self) -> "_HealthyParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://ok.example/0",
                    title="Job 0",
                    source="healthy",
                    language="en",
                )
            ]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="good description")

    def _registry(parser_type: str) -> type[Parser] | None:
        if parser_type == "dead":
            return _DeadParser  # type: ignore[return-value]
        if parser_type == "healthy":
            return _HealthyParser  # type: ignore[return-value]
        return None

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api"), SourceEntry(parser_type="dead"), SourceEntry(parser_type="healthy")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    summary = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=_registry,
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    assert summary.parsers_dead == 1
    assert summary.discovered == 1
    assert summary.written == 1


def test_append_failure_exits_nonzero_position_not_marked_seen(tmp_path: Path) -> None:
    """ResultsFileError from append: run raises (non-zero exit), position NOT marked seen."""
    seen_path = tmp_path / ".seen.json"

    rm = MagicMock()
    rm.ensure_initialized.return_value = None
    rm.next_position_number.return_value = 1
    rm.append.side_effect = ResultsFileError("disk full")

    with pytest.raises(ResultsFileError):
        run(
            _two_stub_config(tmp_path),
            extractor=_stub_extractor(),
            parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(seen_path),
            results_manager=rm,
        )

    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    # No position must be marked kept
    kept = [url for url, rec in seen_data.items() if rec.get("status") == "kept"]
    assert kept == []


# ---------------------------------------------------------------------------
# Run Divider (issue #116)
# ---------------------------------------------------------------------------


def test_integration_run_divider_appended_on_success(tmp_path: Path) -> None:
    """Successful run appends a Run Divider HTML comment with all expected metric keys."""
    seen_path = tmp_path / ".seen.json"
    results_path = tmp_path / "current.md"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )

    run(
        config_path,
        extractor=_FakeExtractor(),
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=ResultsFileManager(results_path, "# Results\n\n"),
    )

    content = results_path.read_text(encoding="utf-8")
    # File ends with a Run Divider (strip trailing newline before split)
    last_block = content.rstrip("\n").rsplit("\n", 1)[-1]
    assert last_block.startswith("<!-- run "), (
        f"last line is not a run divider: {last_block!r}"
    )

    # All expected metric keys present
    for key in (
        "kept=",
        "errors=",
        "classify_calls=",
        "classify_items=",
        "classify_total_s=",
        "judge_calls=",
        "judge_total_s=",
        "claude_input_tokens=",
        "claude_output_tokens=",
        "claude_cache_read_tokens=",
        "claude_cost_usd=",
        "elapsed_s=",
    ):
        assert key in last_block, (
            f"metric key {key!r} missing from divider: {last_block!r}"
        )

    # Per-source count present for the stub source
    assert "sources=stub:" in last_block, f"sources key missing: {last_block!r}"

    # Divider is a well-formed HTML comment
    assert last_block.endswith(" -->"), f"divider not closed: {last_block!r}"


def test_crashed_run_does_not_write_run_divider(tmp_path: Path) -> None:
    """An exception escaping the main run path does not produce a Run Divider."""
    results_path = tmp_path / "current.md"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    class _CrashingExtractor:
        def prewarm(self) -> None:
            pass

        def classify_relevance_batch(
            self, language: str, items: list[ClassifyItem]
        ) -> tuple[list[RelevanceVerdict], CallUsage]:
            raise RuntimeError("unexpected crash escaping main path")

        def judge_match(
            self, language: str, raw_description: str
        ) -> tuple[MatchVerdict, CallUsage]:  # pragma: no cover
            raise NotImplementedError

    with pytest.raises(RuntimeError):
        run(
            config_path,
            extractor=_CrashingExtractor(),
            parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
            results_manager=ResultsFileManager(results_path, "# Results\n\n"),
        )

    content = results_path.read_text(encoding="utf-8")
    assert "<!-- run " not in content, (
        "run divider must not be written when run crashes"
    )


# ---------------------------------------------------------------------------
# Failure Report (issue #117)
# ---------------------------------------------------------------------------


def test_fatal_error_writes_failure_report_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ExtractorUnreachableError → failure report written, stage=orchestrator, exit 1."""
    monkeypatch.chdir(tmp_path)
    config_path = _write_config(tmp_path)
    monkeypatch.setattr("sys.argv", ["app", str(config_path)])

    class _FailingExtractor:
        def prewarm(self) -> None:
            raise ExtractorUnreachableError("test: extractor unreachable")

    monkeypatch.setattr(
        "application_pipeline.orchestrator.ClaudeExtractor",
        lambda *a, **kw: _FailingExtractor(),
    )

    from application_pipeline.__main__ import main

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1

    failures_dir = tmp_path / "failures"
    reports = list(failures_dir.glob("*.md"))
    assert len(reports) == 1, f"expected one failure report, got {reports}"

    body = reports[0].read_text(encoding="utf-8")
    assert "orchestrator" in body
    assert "startup failed" in body  # log tail captured before exception propagated


def test_results_write_stage_label_on_append_failure(tmp_path: Path) -> None:
    """ResultsFileError in step 12 → current_stage set to 'results_write'."""
    from application_pipeline.orchestrator import current_stage

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )
    crashing_rm = MagicMock()
    crashing_rm.ensure_initialized.return_value = None
    crashing_rm.next_position_number.return_value = 1
    crashing_rm.append.side_effect = ResultsFileError("disk full")

    token = current_stage.set("orchestrator")
    try:
        with pytest.raises(ResultsFileError):
            run(
                config_path,
                extractor=_FakeExtractor(),
                parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
                dedup_store=dedup_module.load(tmp_path / ".seen.json"),
                results_manager=crashing_rm,
            )

        assert current_stage.get() == "results_write"
    finally:
        current_stage.reset(token)


# ---------------------------------------------------------------------------
# parser_log integration
# ---------------------------------------------------------------------------


def test_parser_log_integration(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """parser started line + SUMMARY with discovered= and duration= appear in the log file."""
    import logging

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    with caplog.at_level(logging.INFO, logger="application_pipeline.orchestrator"):
        run(
            config_path,
            extractor=_stub_extractor(),
            parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
            results_manager=_stub_results_manager(),
        )

    log_file = logs_dir / "bundesagentur_api.log"
    assert log_file.exists(), "parser log file must be created"

    content = log_file.read_text(encoding="utf-8")
    assert "parser started" in content
    assert "SUMMARY OF SESSION" in content
    assert "discovered=" in content
    assert "duration=" in content

    assert any(
        "parser bundesagentur_api started" in record.message
        for record in caplog.records
    ), "INFO line 'parser <id> started' must appear in stderr"


def test_not_served_queries_counted_in_parser_log_summary(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Three NotServedQuery sentinels → not_served_queries=3 in SUMMARY, nothing in body, no stderr."""
    import logging

    import application_pipeline.parser_log as parser_log
    from application_pipeline.parsers import NotServedQuery

    class _NotServedParser:
        def __enter__(self) -> "_NotServedParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[NotServedQuery]:
            return [NotServedQuery()]

        def enrich(self, stub: PositionStub) -> Position:
            raise AssertionError("enrich must not be called")

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    # 3 keywords × 1 location × no remote = 3 discover() calls → 3 sentinels
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python", "java", "rust"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    with caplog.at_level(logging.DEBUG):
        run(
            config_path,
            extractor=_stub_extractor(),
            parser_registry=lambda _: _NotServedParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
            results_manager=_stub_results_manager(),
        )

    log_file = logs_dir / "bundesagentur_api.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")

    # Body (before SUMMARY) must have no not_served text
    body = content.split("SUMMARY OF SESSION")[0]
    assert "not_served" not in body

    # SUMMARY must contain not_served_queries=3
    assert "SUMMARY OF SESSION" in content
    assert "not_served_queries=3" in content

    # No stderr log records mentioning not_served
    assert not any("not_served" in record.message for record in caplog.records), (
        "not_served must not appear in any stderr log record"
    )


def test_parser_log_records_enrich_failed_redirect_and_dead(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ParserError, ExternalRedirect, and _ParserDead each produce an entry in the parser log; no WARNING/ERROR on stderr; SUMMARY includes all three counters."""
    import logging

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    _STUB_URLS = [
        "https://stub.example/0",
        "https://stub.example/1",
    ]

    class _ThreeEventParser:
        def __enter__(self) -> "_ThreeEventParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            yield PositionStub(
                url=_STUB_URLS[0], title="Job 0", source="stub", language="en"
            )
            yield PositionStub(
                url=_STUB_URLS[1], title="Job 1", source="stub", language="en"
            )
            raise RuntimeError("thread crashed")

        def enrich(self, stub: PositionStub) -> Position | ExternalRedirect:
            if stub.url == _STUB_URLS[0]:
                raise ParserError("enrich boom")
            return ExternalRedirect(
                stub=stub, outbound_url="https://external.example/job"
            )

    with caplog.at_level(logging.WARNING, logger="application_pipeline.orchestrator"):
        summary = run(
            _write_config(
                tmp_path,
                sources='[SourceEntry(parser_type="bundesagentur_api")]',
                keywords='["python"]',
                locations='["Hamburg"]',
                include_remote=False,
            ),
            extractor=_stub_extractor(),
            parser_registry=lambda _: _ThreeEventParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
            results_manager=_stub_results_manager(),
        )

    assert summary.enrich_failed == 1
    assert summary.external_redirects == 1
    assert summary.parsers_dead == 1

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records == [], f"unexpected WARNING/ERROR(s): {warning_records}"

    log_file = logs_dir / "bundesagentur_api.log"
    assert log_file.exists(), "parser log file must be created"
    content = log_file.read_text(encoding="utf-8")

    assert "enrich_failed" in content
    assert "external_redirect" in content
    assert "traceback" in content

    assert "enrich_failed=1" in content
    assert "external_redirects=1" in content
    assert "parsers_dead=1" in content


# ---------------------------------------------------------------------------
# language.log integration
# ---------------------------------------------------------------------------


def test_language_log_anomaly_entries(tmp_path: Path) -> None:
    """German position → no anomaly; French position → detected=other; short → detected=unknown.

    Exactly one SUMMARY trailer must appear in language.log.
    Downstream classify input uses effective language in {de, en} only.
    """
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    _DE_DESCRIPTION = (
        "Wir suchen einen erfahrenen Softwareentwickler für unser Team in Hamburg. "
        "Das Unternehmen bietet interessante Projekte und eine gute Bezahlung. "
        "Bewerben Sie sich jetzt mit Ihren vollständigen Unterlagen."
    )
    _FR_DESCRIPTION = (
        "Nous recherchons un ingénieur logiciel expérimenté pour rejoindre notre équipe. "
        "L'entreprise offre des projets intéressants et une bonne rémunération. "
        "Postulez maintenant avec vos documents complets."
    )
    _SHORT_DESCRIPTION = "456 789 012"

    _LANGUAGE_STUB_URLS = [
        "https://lang.example/de",
        "https://lang.example/fr",
        "https://lang.example/short",
    ]

    class _LanguageTestParser:
        def __enter__(self) -> "_LanguageTestParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=_LANGUAGE_STUB_URLS[0],
                    title="Softwareentwickler",
                    source="test",
                ),
                PositionStub(
                    url=_LANGUAGE_STUB_URLS[1],
                    title="Ingénieur logiciel",
                    source="test",
                ),
                PositionStub(url=_LANGUAGE_STUB_URLS[2], title="123", source="test"),
            ]

        def enrich(self, stub: PositionStub) -> Position:
            if stub.url == _LANGUAGE_STUB_URLS[0]:
                return Position(stub=stub, raw_description=_DE_DESCRIPTION)
            elif stub.url == _LANGUAGE_STUB_URLS[1]:
                return Position(stub=stub, raw_description=_FR_DESCRIPTION)
            else:
                return Position(stub=stub, raw_description=_SHORT_DESCRIPTION)

    captured_languages: list[str] = []

    class _CapturingExtractor:
        def prewarm(self) -> None:
            pass

        def classify_relevance_batch(
            self, language: str, items: list[ClassifyItem]
        ) -> "tuple[list[RelevanceVerdict], CallUsage]":
            captured_languages.append(language)
            return [RelevanceVerdict(in_domain=True) for _ in items], _ZERO_USAGE

        def judge_match(
            self, language: str, raw_description: str
        ) -> "tuple[MatchVerdict, CallUsage]":
            return (
                MatchVerdict(
                    tier=MatchTier.green, matched=[], missing=[], summary="ok"
                ),
                _ZERO_USAGE,
            )

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    run(
        config_path,
        extractor=_CapturingExtractor(),  # type: ignore[arg-type]
        parser_registry=lambda _: _LanguageTestParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    # Downstream classify only receives "de" or "en"
    assert all(lang in ("de", "en") for lang in captured_languages), (
        f"classify_relevance must only receive 'de' or 'en', got: {captured_languages}"
    )

    log_file = logs_dir / "language.log"
    assert log_file.exists(), "language.log must be created"

    content = log_file.read_text(encoding="utf-8")

    # German position produces no anomaly entry
    assert "lang.example/de" not in content

    # French position: detected=other anomaly
    assert "lang.example/fr" in content
    assert "detected=other" in content

    # Short position: detected=unknown anomaly
    assert "lang.example/short" in content
    assert "detected=unknown" in content

    # Exactly one SUMMARY trailer
    assert content.count("SUMMARY OF SESSION") == 1
    assert "anomalies=2" in content


# ---------------------------------------------------------------------------
# Position._warnings — field existence and round-trip
# ---------------------------------------------------------------------------


def test_position_warnings_defaults_to_empty_tuple() -> None:
    stub = PositionStub(url="https://x.com/1", title="T", source="s")
    pos = Position(stub=stub, raw_description="desc")
    assert pos._warnings == ()


def test_position_warnings_round_trips_through_construction() -> None:
    stub = PositionStub(url="https://x.com/1", title="T", source="s")
    pos = Position(
        stub=stub,
        raw_description="desc",
        _warnings=("unparseable_date raw=INVALID",),
    )
    assert pos._warnings == ("unparseable_date raw=INVALID",)


# ---------------------------------------------------------------------------
# Orchestrator: Position._warnings drained to parser_log + SUMMARY
# ---------------------------------------------------------------------------


class _WarnParser:
    """Returns one stub; enrich returns a Position with an unparseable_date warning."""

    def __enter__(self) -> "_WarnParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return [
            PositionStub(
                url="https://stub.example/warn/1", title="Warn Job", source="stub"
            )
        ]

    def enrich(self, stub: PositionStub) -> Position:
        return Position(
            stub=stub,
            raw_description="some description",
            posted_date=None,
            _warnings=("unparseable_date raw=INVALID_DATE",),
        )


def test_unparseable_date_warning_routed_to_parser_log(tmp_path: Path) -> None:
    """Position._warnings are drained into parser_log before classify;
    the resulting Position still reaches downstream with posted_date=None."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="jobs_beim_staat_html")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    summary = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _WarnParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    log_file = logs_dir / "jobs_beim_staat_html.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "unparseable_date raw=INVALID_DATE" in content
    assert "unparseable_dates=1" in content
    assert summary.written == 1


def test_unparseable_date_warning_not_emitted_to_stderr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The unparseable_date warning must NOT appear in stderr (the old _log.info path)."""
    import logging

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="jobs_beim_staat_html")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    with caplog.at_level(logging.INFO):
        run(
            config_path,
            extractor=_stub_extractor(),
            parser_registry=lambda _: _WarnParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
            results_manager=_stub_results_manager(),
        )

    assert not any("unparseable_date" in record.message for record in caplog.records), (
        "unparseable_date must not appear in logging output"
    )


# ---------------------------------------------------------------------------
# Batched classify pipeline — new tests for issue #187
# ---------------------------------------------------------------------------


def _batch_size_config(tmp_path: Path, batch_size: int) -> Path:
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    config_text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config_text + f"\nCLAUDE_CLASSIFY_BATCH_SIZE = {batch_size}\n",
        encoding="utf-8",
    )
    return config_path


def test_batch_flush_at_size(tmp_path: Path) -> None:
    """batch_size=2 with 4 positions → classify_relevance_batch called twice with 2 items each."""
    batch_sizes_seen: list[int] = []

    def _batch(
        lang: str, items: list[ClassifyItem]
    ) -> tuple[list[RelevanceVerdict], CallUsage]:
        batch_sizes_seen.append(len(items))
        return [RelevanceVerdict(in_domain=True) for _ in items], _ZERO_USAGE

    ext = MagicMock()
    ext.prewarm.return_value = None
    ext.classify_relevance_batch.side_effect = _batch
    ext.judge_match.return_value = (
        MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok"),
        _ZERO_USAGE,
    )

    class _FourStubParser:
        def __enter__(self) -> "_FourStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=f"https://batch.example/{i}",
                    title=f"Job {i}",
                    source="stub",
                    language="en",
                )
                for i in range(4)
            ]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="good description")

    summary = run(
        _batch_size_config(tmp_path, 2),
        extractor=ext,
        parser_registry=lambda _: _FourStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    assert summary.written == 4
    # With batch_size=2 and 4 items: 2 batches
    assert batch_sizes_seen == [2, 2]


def test_language_routing_de_and_en_buffers(tmp_path: Path) -> None:
    """German positions go to de classify call, English positions go to en classify call."""
    lang_batches: dict[str, list[int]] = {}

    def _batch(
        lang: str, items: list[ClassifyItem]
    ) -> tuple[list[RelevanceVerdict], CallUsage]:
        lang_batches[lang] = lang_batches.get(lang, []) + [len(items)]
        return [RelevanceVerdict(in_domain=True) for _ in items], _ZERO_USAGE

    ext = MagicMock()
    ext.prewarm.return_value = None
    ext.classify_relevance_batch.side_effect = _batch
    ext.judge_match.return_value = (
        MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok"),
        _ZERO_USAGE,
    )

    _DE_DESCRIPTION = (
        "Wir suchen einen erfahrenen Softwareentwickler für unser Team. "
        "Das Unternehmen bietet interessante Projekte und eine gute Bezahlung."
    )

    class _MixedLangParser:
        def __enter__(self) -> "_MixedLangParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://ml.example/de1", title="Entwickler", source="s"
                ),
                PositionStub(
                    url="https://ml.example/de2", title="Ingenieur", source="s"
                ),
                PositionStub(
                    url="https://ml.example/en1",
                    title="Engineer",
                    source="s",
                    language="en",
                ),
                PositionStub(
                    url="https://ml.example/en2",
                    title="Developer",
                    source="s",
                    language="en",
                ),
            ]

        def enrich(self, stub: PositionStub) -> Position:
            if "de" in stub.url:
                return Position(stub=stub, raw_description=_DE_DESCRIPTION)
            return Position(
                stub=stub, raw_description="Software engineering role in English."
            )

    summary = run(
        _batch_size_config(tmp_path, 100),
        extractor=ext,
        parser_registry=lambda _: _MixedLangParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    assert summary.written == 4
    assert "de" in lang_batches
    assert "en" in lang_batches
    assert lang_batches["de"] == [2]
    assert lang_batches["en"] == [2]


def test_off_domain_marked_seen_immediately_no_judge(tmp_path: Path) -> None:
    """Positions classified as off-domain are marked seen immediately; judge_match is NOT called."""
    seen_path = tmp_path / ".seen.json"
    _OFF_URL = "https://offdomain.example/0"
    _ON_URL = "https://offdomain.example/1"

    def _batch(
        lang: str, items: list[ClassifyItem]
    ) -> tuple[list[RelevanceVerdict], CallUsage]:
        # First item off-domain, second in-domain
        return [
            RelevanceVerdict(in_domain=(item.raw_description != "off domain content"))
            for item in items
        ], _ZERO_USAGE

    ext = MagicMock()
    ext.prewarm.return_value = None
    ext.classify_relevance_batch.side_effect = _batch
    ext.judge_match.return_value = (
        MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok"),
        _ZERO_USAGE,
    )

    class _TwoLangParser:
        def __enter__(self) -> "_TwoLangParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=_OFF_URL, title="Off-domain Job", source="s", language="en"
                ),
                PositionStub(
                    url=_ON_URL, title="On-domain Job", source="s", language="en"
                ),
            ]

        def enrich(self, stub: PositionStub) -> Position:
            if stub.url == _OFF_URL:
                return Position(stub=stub, raw_description="off domain content")
            return Position(stub=stub, raw_description="software engineering role")

    summary = run(
        _batch_size_config(tmp_path, 100),
        extractor=ext,
        parser_registry=lambda _: _TwoLangParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=_stub_results_manager(),
    )

    assert summary.classifier_dropped == 1
    assert summary.written == 1
    # judge_match called only once (for the in-domain position)
    assert ext.judge_match.call_count == 1

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_OFF_URL]["status"] == "off_domain"


def test_batch_malformed_no_items_marked_seen(tmp_path: Path) -> None:
    """ExtractorBatchMalformedError: none of the batch items are marked seen; run continues."""
    seen_path = tmp_path / ".seen.json"

    call_count = [0]

    def _batch(
        lang: str, items: list[ClassifyItem]
    ) -> tuple[list[RelevanceVerdict], CallUsage]:
        call_count[0] += 1
        if call_count[0] == 1:
            raise ExtractorBatchMalformedError("length mismatch")
        return [RelevanceVerdict(in_domain=True) for _ in items], _ZERO_USAGE

    ext = MagicMock()
    ext.prewarm.return_value = None
    ext.classify_relevance_batch.side_effect = _batch
    ext.judge_match.return_value = (
        MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok"),
        _ZERO_USAGE,
    )

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    config_text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config_text + "\nCLAUDE_CLASSIFY_BATCH_SIZE = 1\n", encoding="utf-8"
    )

    summary = run(
        config_path,
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=_stub_results_manager(),
    )

    # First batch failed (1 item), second succeeded (1 item written)
    assert summary.errored == 1
    assert summary.written == 1

    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    # First item must NOT be in seen store
    assert _ERR_URLS[0] not in seen_data


def test_batch_malformed_logs_to_classify_relevance_log(tmp_path: Path) -> None:
    """ExtractorBatchMalformedError is logged to classify_relevance.log via parser_log."""
    import application_pipeline.parser_log as pl

    logs_dir = tmp_path / "synched" / "logs"
    pl.configure(logs_dir)

    def _batch(lang: str, items: list[ClassifyItem]) -> list[RelevanceVerdict]:
        raise ExtractorBatchMalformedError("id mismatch")

    ext = MagicMock()
    ext.prewarm.return_value = None
    ext.classify_relevance_batch.side_effect = _batch

    run(
        _batch_size_config(tmp_path, 100),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    log_file = logs_dir / "classify_relevance.log"
    assert log_file.exists(), "classify_relevance.log must be created on batch error"
    content = log_file.read_text(encoding="utf-8")
    assert "batch_error" in content


def test_claude_usage_limit_error_propagates_from_run(tmp_path: Path) -> None:
    """ClaudeUsageLimitError during classify propagates from run(); in-flight items remain unmarked."""
    seen_path = tmp_path / ".seen.json"

    def _batch(lang: str, items: list[ClassifyItem]) -> list[RelevanceVerdict]:
        raise ClaudeUsageLimitError("subscription cap")

    ext = MagicMock()
    ext.prewarm.return_value = None
    ext.classify_relevance_batch.side_effect = _batch

    with pytest.raises(ClaudeUsageLimitError):
        run(
            _two_stub_config(tmp_path),
            extractor=ext,
            parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(seen_path),
            results_manager=_stub_results_manager(),
        )

    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    # No items must be marked seen (they were in-flight when abort happened)
    assert all(url not in seen_data for url in _ERR_URLS[:2])


def test_claude_usage_limit_error_writes_failure_report_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ClaudeUsageLimitError during classify → failure report written, exit 1."""
    monkeypatch.chdir(tmp_path)
    config_path = _write_config(tmp_path)
    monkeypatch.setattr("sys.argv", ["app", str(config_path)])

    def _batch(lang: str, items: list[ClassifyItem]) -> list[RelevanceVerdict]:
        raise ClaudeUsageLimitError("subscription cap")

    class _UsageLimitExtractor:
        def prewarm(self) -> None:
            pass

        def classify_relevance_batch(
            self, language: str, items: list[ClassifyItem]
        ) -> tuple[list[RelevanceVerdict], CallUsage]:
            raise ClaudeUsageLimitError("subscription cap")

        def judge_match(
            self, language: str, raw_description: str
        ) -> tuple[MatchVerdict, CallUsage]:  # pragma: no cover
            raise NotImplementedError

    monkeypatch.setattr(
        "application_pipeline.orchestrator.ClaudeExtractor",
        lambda *a, **kw: _UsageLimitExtractor(),
    )

    from application_pipeline.__main__ import main

    # Need a parser that produces at least one position so classify_relevance_batch is called
    class _OneStubParser:
        def __enter__(self) -> "_OneStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://limit.example/0",
                    title="Job",
                    source="s",
                    language="en",
                )
            ]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="software engineering role")

    monkeypatch.setattr(
        "application_pipeline.orchestrator._default_registry",
        type("_Reg", (), {"get": staticmethod(lambda _: _OneStubParser)})(),
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1

    failures_dir = tmp_path / "failures"
    reports = list(failures_dir.glob("*.md"))
    assert len(reports) == 1, f"expected one failure report, got {reports}"


# ---------------------------------------------------------------------------
# Prompt loader: only de + en; init materialises only de + en
# ---------------------------------------------------------------------------


def test_prompt_loader_only_de_and_en_for_classify(tmp_path: Path) -> None:
    """load_prompts only loads classify_relevance.de.md and classify_relevance.en.md."""
    from application_pipeline.prompts import load_prompts
    from application_pipeline import Config, SourceEntry

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "classify_relevance.de.md").write_text("{ITEMS}", encoding="utf-8")
    (prompts_dir / "classify_relevance.en.md").write_text("{ITEMS}", encoding="utf-8")
    (prompts_dir / "judge_match.de.md").write_text(
        "{skills} {raw_description}", encoding="utf-8"
    )
    (prompts_dir / "judge_match.en.md").write_text(
        "{skills} {raw_description}", encoding="utf-8"
    )

    cfg = Config(
        keywords=["python"],
        skills=["python"],
        sources=[SourceEntry(parser_type="bundesagentur_api")],
        locations=["Hamburg"],
        prompts_dir=prompts_dir,
    )
    prompts = load_prompts(cfg)

    assert set(prompts.classify_relevance.keys()) == {"de", "en"}
    assert set(prompts.judge_match.keys()) == {"de", "en"}


def test_init_does_not_materialise_other_or_unknown_prompt_files(
    tmp_path: Path,
) -> None:
    """init command copies only de + en prompt files; no 'other' or 'unknown' files."""
    from application_pipeline.init_cmd import init

    init(tmp_path)

    prompt_files = list((tmp_path / "prompts").glob("*.md"))
    names = {f.name for f in prompt_files}

    assert "classify_relevance.other.md" not in names
    assert "classify_relevance.unknown.md" not in names
    assert "judge_match.other.md" not in names
    assert "judge_match.unknown.md" not in names
    assert "classify_relevance.de.md" in names
    assert "classify_relevance.en.md" in names
    assert "judge_match.de.md" in names
    assert "judge_match.en.md" in names


# ---------------------------------------------------------------------------
# RunSummary telemetry (issue #188)
# ---------------------------------------------------------------------------


def test_run_summary_carries_token_and_cost_totals(tmp_path: Path) -> None:
    """RunSummary accumulates classify + judge token/cost totals from _FakeExtractor."""
    seen_path = tmp_path / ".seen.json"
    results_path = tmp_path / "current.md"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )

    summary = run(
        config_path,
        extractor=_FakeExtractor(),
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=ResultsFileManager(results_path, "# Results\n\n"),
    )

    # 5 items pass prefilter → 1 classify batch
    assert summary.classify_items == 5
    # 1 classify batch × 10 input tokens + 4 judge calls × 8 input tokens
    assert summary.claude_input_tokens == 10 + 4 * 8
    assert summary.claude_output_tokens == 5 + 4 * 4
    assert summary.claude_cache_read_tokens == 2 + 4 * 1
    assert abs(summary.claude_cost_usd - (0.001 + 4 * 0.0008)) < 1e-9


def test_classify_relevance_trailer_schema(tmp_path: Path) -> None:
    """classify_relevance.log gets a SUMMARY OF SESSION with the full expected schema."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    seen_path = tmp_path / ".seen.json"
    results_path = tmp_path / "current.md"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )

    run(
        config_path,
        extractor=_FakeExtractor(),
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=ResultsFileManager(results_path, "# Results\n\n"),
    )

    log_file = logs_dir / "classify_relevance.log"
    assert log_file.exists(), "classify_relevance.log must be created"
    content = log_file.read_text(encoding="utf-8")

    assert "SUMMARY OF SESSION" in content
    for key in (
        "batches_sent=",
        "items_classified=",
        "in_domain=",
        "off_domain=",
        "batches_failed=",
        "input_tokens=",
        "output_tokens=",
        "cache_read_tokens=",
        "cost_usd=",
        "duration_s=",
    ):
        assert key in content, f"key {key!r} missing from classify_relevance.log"


def test_judge_match_trailer_schema(tmp_path: Path) -> None:
    """judge_match.log gets a SUMMARY OF SESSION with the full expected schema."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    seen_path = tmp_path / ".seen.json"
    results_path = tmp_path / "current.md"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )

    run(
        config_path,
        extractor=_FakeExtractor(),
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=ResultsFileManager(results_path, "# Results\n\n"),
    )

    log_file = logs_dir / "judge_match.log"
    assert log_file.exists(), "judge_match.log must be created"
    content = log_file.read_text(encoding="utf-8")

    assert "SUMMARY OF SESSION" in content
    for key in (
        "judges_sent=",
        "judges_failed=",
        "green=",
        "amber=",
        "red=",
        "input_tokens=",
        "output_tokens=",
        "cache_read_tokens=",
        "cost_usd=",
        "duration_s=",
    ):
        assert key in content, f"key {key!r} missing from judge_match.log"


def test_main_run_complete_line_includes_new_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """__main__ 'run complete:' line includes classify_items, claude_* token and cost fields."""
    monkeypatch.chdir(tmp_path)
    config_path = _write_config(tmp_path)
    monkeypatch.setattr("sys.argv", ["app", str(config_path)])

    fake_summary = RunSummary(
        discovered=3,
        written=2,
        classify_items=3,
        claude_input_tokens=42,
        claude_output_tokens=21,
        claude_cache_read_tokens=6,
        claude_cost_usd=0.0042,
        duration_seconds=1.5,
    )
    monkeypatch.setattr(
        "application_pipeline.__main__.run", lambda _path, **_kw: fake_summary
    )

    from application_pipeline.__main__ import main

    main()

    out = capsys.readouterr().out
    assert out.startswith("run complete:"), (
        f"expected 'run complete:' prefix, got: {out!r}"
    )
    for field in (
        "classify_items=",
        "claude_input_tokens=",
        "claude_output_tokens=",
        "claude_cache_read_tokens=",
        "claude_cost_usd=",
    ):
        assert field in out, f"field {field!r} missing from run complete line: {out!r}"


# ---------------------------------------------------------------------------
# StatusDisplay wiring
# ---------------------------------------------------------------------------


def test_display_pipeline_row_registered_on_run(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    assert "pipeline" in display.registered_names()
    register_call = next(
        c for c in display.calls if c.method == "register" and c.name == "pipeline"
    )
    assert register_call.kwargs["order"] == 0


def test_display_body_updated_with_discovered_during_run(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    bodies = display.body_updates_for("pipeline")
    assert len(bodies) > 0
    assert any("discovered=" in b for b in bodies)
    assert any("written=" in b for b in bodies)
    assert any("errors=" in b for b in bodies)


def test_display_stop_called_on_success(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    assert display.stopped


def test_display_stop_called_on_crash(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    display = FakeStatusDisplay()
    failing = MagicMock()
    failing.prewarm.side_effect = ExtractorUnreachableError("down")

    with pytest.raises(ExtractorUnreachableError):
        run(
            config_path,
            extractor=failing,
            dedup_store=MagicMock(),
            results_manager=_stub_results_manager(),
            status_display=display,
        )

    assert display.stopped


def test_display_parser_log_records_pipeline_register(tmp_path: Path) -> None:
    """pipeline register() writes a record to pipeline.log via parser_log."""
    _parser_log.configure(tmp_path / "logs")
    config_path = _write_config(tmp_path)

    from application_pipeline.status_display import PlainStatusDisplay

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
        results_manager=_stub_results_manager(),
        status_display=PlainStatusDisplay(),
    )

    log_content = (tmp_path / "logs" / "pipeline.log").read_text(encoding="utf-8")
    assert "registered" in log_content
    assert "order=0" in log_content


# ---------------------------------------------------------------------------
# StatusDisplay — startup row
# ---------------------------------------------------------------------------


def test_startup_row_ordering(tmp_path: Path) -> None:
    """register("startup") precedes any parser-row registration;
    remove("startup") precedes the first PositionStub-triggered pipeline body update."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    indexed = [(i, c.method, c.name) for i, c in enumerate(display.calls)]

    pipeline_register_idx = next(
        i for i, m, n in indexed if m == "register" and n == "pipeline"
    )
    startup_register_idx = next(
        i for i, m, n in indexed if m == "register" and n == "startup"
    )
    remove_startup_idx = next(
        i for i, m, n in indexed if m == "remove" and n == "startup"
    )
    first_pipeline_body_idx = next(
        i for i, m, n in indexed if m == "update_body" and n == "pipeline"
    )

    assert startup_register_idx > pipeline_register_idx
    assert remove_startup_idx < first_pipeline_body_idx


def test_startup_row_visible_on_prewarm_failure(tmp_path: Path) -> None:
    """startup row is not removed and shows a meaningful body when prewarm fails."""
    config_path = _write_config(tmp_path)
    display = FakeStatusDisplay()
    failing = MagicMock()
    failing.prewarm.side_effect = ExtractorUnreachableError("down")

    with pytest.raises(ExtractorUnreachableError):
        run(
            config_path,
            extractor=failing,
            dedup_store=MagicMock(),
            results_manager=_stub_results_manager(),
            status_display=display,
        )

    assert not any(c.method == "remove" and c.name == "startup" for c in display.calls)
    startup_bodies = display.body_updates_for("startup")
    assert startup_bodies and "prewarm" in startup_bodies[-1]


# ---------------------------------------------------------------------------
# StatusDisplay — per-parser rows (issue #197)
# ---------------------------------------------------------------------------


def test_parser_row_registered_per_parser(tmp_path: Path) -> None:
    """One status display row is registered per parser, with order >= 2."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    assert "bundesagentur_api" in display.registered_names()
    reg = next(
        c
        for c in display.calls
        if c.method == "register" and c.name == "bundesagentur_api"
    )
    assert reg.kwargs["order"] >= 2


def test_parser_row_registered_after_startup(tmp_path: Path) -> None:
    """Parser row is registered after pipeline and startup rows."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    indexed = [(i, c.method, c.name) for i, c in enumerate(display.calls)]
    startup_reg_idx = next(
        i for i, m, n in indexed if m == "register" and n == "startup"
    )
    parser_reg_idx = next(
        i for i, m, n in indexed if m == "register" and n == "bundesagentur_api"
    )
    assert parser_reg_idx > startup_reg_idx


def test_parser_row_body_ends_with_done(tmp_path: Path) -> None:
    """Parser row body gains '· done' suffix when parser completes; row is not removed."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    bodies = display.body_updates_for("bundesagentur_api")
    assert bodies, "expected at least one body update for parser row"
    assert bodies[-1].endswith("· done"), (
        f"last body {bodies[-1]!r} must end with '· done'"
    )
    assert not any(
        c.method == "remove" and c.name == "bundesagentur_api" for c in display.calls
    ), "parser row must not be removed during run"


def test_parser_row_body_tracks_queries_stubs_enriched(tmp_path: Path) -> None:
    """Parser row body contains queries/total, stubs, and enriched counts."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    bodies = display.body_updates_for("bundesagentur_api")
    # Last body before "done" should have format: "X/Y queries · N stubs · M enriched · done"
    final = bodies[-1]
    assert "queries" in final
    assert "stubs" in final
    assert "enriched" in final
    # _StubParser returns 3 stubs per call; 1 keyword × 1 location = 1 query → 3 stubs, 3 enriched
    assert final.startswith("1/1 queries · 3 stubs · 3 enriched")


def test_parser_row_body_shows_dead_on_crash(tmp_path: Path) -> None:
    """Parser row body gains '· dead' suffix when parser thread crashes."""

    class _DeadParserForRow:
        def __enter__(self) -> "_DeadParserForRow":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            raise RuntimeError("intentional crash")
            yield  # pragma: no cover

        def enrich(self, stub: PositionStub) -> Position:  # pragma: no cover
            raise NotImplementedError

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _DeadParserForRow,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    bodies = display.body_updates_for("bundesagentur_api")
    assert bodies, "expected at least one body update for dead parser row"
    assert bodies[-1].endswith("· dead"), (
        f"last body {bodies[-1]!r} must end with '· dead'"
    )
    assert not any(
        c.method == "remove" and c.name == "bundesagentur_api" for c in display.calls
    ), "dead parser row must not be removed"


def test_multiple_parser_rows_each_registered(tmp_path: Path) -> None:
    """Multiple parsers each get their own row with distinct order values."""

    class _EmptyParser:
        def __enter__(self) -> "_EmptyParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return []

        def enrich(self, stub: PositionStub) -> Position:  # pragma: no cover
            raise NotImplementedError

    # Use two real parser_type names so location coverage validation passes
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api"), SourceEntry(parser_type="stellen_hamburg_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _EmptyParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    registered = display.registered_names()
    assert "bundesagentur_api" in registered
    assert "stellen_hamburg_api" in registered

    order_a = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "bundesagentur_api"
    )
    order_b = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "stellen_hamburg_api"
    )
    assert order_a >= 2
    assert order_b >= 2
    assert order_a != order_b


# ---------------------------------------------------------------------------
# Status Display: dedup and prefilter rows
# ---------------------------------------------------------------------------


def test_dedup_and_prefilter_rows_registered(tmp_path: Path) -> None:
    """dedup and prefilter rows are registered after all parser rows."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    registered = display.registered_names()
    assert "dedup" in registered
    assert "prefilter" in registered

    dedup_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "dedup"
    )
    prefilter_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "prefilter"
    )
    parser_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "bundesagentur_api"
    )
    assert dedup_order > parser_order
    assert prefilter_order == dedup_order + 1


def test_dedup_row_body_updates_on_dedup_events(tmp_path: Path) -> None:
    """dedup row body tracks url_hits, tuple_hits, and misses."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    bodies = display.body_updates_for("dedup")
    assert bodies, "expected at least one body update for dedup row"
    final = bodies[-1]
    assert "url_hits=" in final
    assert "tuple_hits=" in final
    assert "misses=" in final
    # _StubParser emits 3 fresh stubs → 3 misses, 0 hits on first run
    assert "misses=3" in final
    assert "url_hits=0" in final
    assert "tuple_hits=0" in final


def test_prefilter_row_body_updates_on_enrich_events(tmp_path: Path) -> None:
    """prefilter row body tracks considered, passed, and dropped counts."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    bodies = display.body_updates_for("prefilter")
    assert bodies, "expected at least one body update for prefilter row"
    final = bodies[-1]
    assert "considered=" in final
    assert "passed=" in final
    assert "dropped=" in final
    assert "wl=" in final
    assert "bl=" in final


def test_dedup_and_prefilter_rows_not_removed(tmp_path: Path) -> None:
    """dedup and prefilter rows persist for the entire run (no mid-run removal)."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    assert not any(c.method == "remove" and c.name == "dedup" for c in display.calls), (
        "dedup row must not be removed during run"
    )
    assert not any(
        c.method == "remove" and c.name == "prefilter" for c in display.calls
    ), "prefilter row must not be removed during run"


# ---------------------------------------------------------------------------
# Status Display: classify_relevance and judge_match rows (issue #199)
# ---------------------------------------------------------------------------

_DE_DESCRIPTION_199 = (
    "Wir suchen einen erfahrenen Softwareentwickler für unser Team. "
    "Das Unternehmen bietet interessante Projekte und eine gute Bezahlung."
)


class _MixedLangParser199:
    """1 de stub + 1 en stub per discover call for classify/judge row tests."""

    def __enter__(self) -> "_MixedLangParser199":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return [
            PositionStub(
                url="https://cl199.example/de1", title="Entwickler", source="s"
            ),
            PositionStub(
                url="https://cl199.example/en1",
                title="Engineer",
                source="s",
                language="en",
            ),
        ]

    def enrich(self, stub: PositionStub) -> Position:
        if "de1" in stub.url:
            return Position(stub=stub, raw_description=_DE_DESCRIPTION_199)
        return Position(stub=stub, raw_description="Software engineering role.")


def test_classify_and_judge_rows_registered(tmp_path: Path) -> None:
    """classify_relevance and judge_match rows are registered below prefilter and adjacent."""
    display = FakeStatusDisplay()

    run(
        _write_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    assert "classify_relevance" in display.registered_names()
    assert "judge_match" in display.registered_names()

    prefilter_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "prefilter"
    )
    classify_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "classify_relevance"
    )
    judge_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "judge_match"
    )

    assert classify_order > prefilter_order
    assert judge_order == classify_order + 1


def test_classify_and_judge_rows_body_progression(tmp_path: Path) -> None:
    """Synthetic (de, en) batch mix: classify_relevance and judge_match bodies progress correctly."""
    display = FakeStatusDisplay()

    run(
        _batch_size_config(tmp_path, 1),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _MixedLangParser199,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    classify_bodies = display.body_updates_for("classify_relevance")
    judge_bodies = display.body_updates_for("judge_match")

    # 1 de + 1 en survivor, batch_size=1 → 2 classify batches
    assert len(classify_bodies) == 2

    # After de batch (1/2 done, 1 en item still in queue)
    assert "1/2 batches done" in classify_bodies[0]
    assert "1 items in queue" in classify_bodies[0]
    assert "1 en" in classify_bodies[0]
    assert "0 de" in classify_bodies[0]

    # After en batch (2/2 done, queue empty)
    assert "2/2 batches done" in classify_bodies[1]
    assert "0 items in queue" in classify_bodies[1]
    assert "0 en" in classify_bodies[1]
    assert "0 de" in classify_bodies[1]

    # Both items in-domain → 2 judge calls (stub extractor returns green)
    assert len(judge_bodies) == 2
    assert "1/1 judgments" in judge_bodies[0]
    assert "green=1" in judge_bodies[0]
    assert "2/2 judgments" in judge_bodies[1]
    assert "green=2" in judge_bodies[1]
    assert "amber=0" in judge_bodies[1]
    assert "red=0" in judge_bodies[1]


def test_classify_and_judge_rows_not_removed(tmp_path: Path) -> None:
    """classify_relevance and judge_match rows persist for the entire run."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    display = FakeStatusDisplay()

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        status_display=display,
    )

    assert not any(
        c.method == "remove" and c.name == "classify_relevance" for c in display.calls
    ), "classify_relevance row must not be removed during run"
    assert not any(
        c.method == "remove" and c.name == "judge_match" for c in display.calls
    ), "judge_match row must not be removed during run"


# ---------------------------------------------------------------------------
# Stuck-thread watchdog
# ---------------------------------------------------------------------------


def test_stall_watchdog_logs_stalled_and_stack_trace(tmp_path: Path) -> None:
    """Parser that sleeps past the stall threshold emits 'stalled' + stack trace in its log."""
    import time

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    _THRESHOLD = 0.05  # 50 ms — fast enough for tests

    class _SleepyParser:
        def __enter__(self) -> "_SleepyParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            time.sleep(_THRESHOLD * 4)  # sleep well past threshold
            return []

        def enrich(self, stub: PositionStub) -> Position:  # pragma: no cover
            raise NotImplementedError

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _SleepyParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        stall_threshold_s=_THRESHOLD,
    )

    log_file = logs_dir / "bundesagentur_api.log"
    assert log_file.exists(), "parser log file must be created"
    content = log_file.read_text(encoding="utf-8")

    assert "stalled" in content, "stalled event must appear in parser log"
    assert "traceback" in content, "stack trace header must appear in parser log"
    assert "File " in content, "stack frame lines must appear in parser log"


def test_stall_watchdog_fires_only_once_per_silence(tmp_path: Path) -> None:
    """Stall is logged at most once per silence period — not on every poll tick."""
    import time

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    _THRESHOLD = 0.05

    class _LongSleepParser:
        def __enter__(self) -> "_LongSleepParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            time.sleep(_THRESHOLD * 8)  # sleep for multiple poll ticks
            return []

        def enrich(self, stub: PositionStub) -> Position:  # pragma: no cover
            raise NotImplementedError

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _LongSleepParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
        stall_threshold_s=_THRESHOLD,
    )

    log_file = logs_dir / "bundesagentur_api.log"
    content = log_file.read_text(encoding="utf-8")

    stalled_count = content.count(" stalled ")
    assert stalled_count == 1, f"expected exactly 1 stalled entry, got {stalled_count}"


# ---------------------------------------------------------------------------
# _ParserThread: query_started / query_ended heartbeats (issue #208)
# ---------------------------------------------------------------------------


def test_query_heartbeats_n_started_and_n_ended(tmp_path: Path) -> None:
    """N queries → exactly N query_started and N query_ended lines in the parser log."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python", "django"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    log_file = logs_dir / "bundesagentur_api.log"
    content = log_file.read_text(encoding="utf-8")

    # 2 keywords × 1 location = 2 queries
    started_count = content.count(" query_started ")
    ended_count = content.count(" query_ended ")
    assert started_count == 2, f"expected 2 query_started lines, got {started_count}"
    assert ended_count == 2, f"expected 2 query_ended lines, got {ended_count}"


def test_query_ended_fires_even_when_discover_raises(tmp_path: Path) -> None:
    """query_ended is written even when discover() raises mid-query (parser dies)."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    parser_log.configure(logs_dir)

    class _RaisingParser:
        def __enter__(self) -> "_RaisingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            yield PositionStub(
                url="https://raise.example/0", title="Job 0", source="stub"
            )
            raise RuntimeError("boom mid-discover")

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="good description")

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    summary = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _RaisingParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        results_manager=_stub_results_manager(),
    )

    assert summary.parsers_dead == 1

    log_file = logs_dir / "bundesagentur_api.log"
    content = log_file.read_text(encoding="utf-8")

    assert " query_started " in content, "query_started must be logged before the crash"
    assert " query_ended " in content, (
        "query_ended must fire even when discover() raises"
    )
