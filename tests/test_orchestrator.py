from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from application_pipeline import dedup as dedup_module
from application_pipeline.config import ConfigError
from application_pipeline.dedup import DedupStoreError
from application_pipeline.llm import (
    ExtractorError,
    ExtractorUnreachableError,
    MatchTier,
    MatchVerdict,
    RelevanceVerdict,
)
from application_pipeline.orchestrator import RunSummary, run
from application_pipeline.parsers import Parser, ParserQuery, Position, PositionStub
from application_pipeline.parsers.errors import ParserError
from application_pipeline.prompts import PromptError
from application_pipeline.results import ResultsFileError, ResultsFileManager


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
    ext.classify_relevance.return_value = RelevanceVerdict(in_domain=True)
    ext.judge_match.return_value = MatchVerdict(
        tier=MatchTier.green, matched=[], missing=[], summary="ok"
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
            sources='[SourceEntry(parser_type="stub")]',
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
            sources='[SourceEntry(parser_type="stub")]',
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
            sources='[SourceEntry(parser_type="stub")]',
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
    assert summary.written == 5

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_REJECTED_URL]["status"] == "off_domain"


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


class _FakeExtractor:
    """Deterministic extractor: rejects Job 1 at classify, returns fixed tiers at judge."""

    def prewarm(self) -> None:
        pass

    def classify_relevance(
        self, language: str, title: str, raw_description: str
    ) -> RelevanceVerdict:
        return RelevanceVerdict(in_domain=(title != "Job 1"))

    def judge_match(self, language: str, raw_description: str) -> MatchVerdict:
        # Extract job index from description ("description for job N")
        for idx, url in enumerate(_STUB_URLS_LLM):
            if f"job {idx}" in raw_description:
                tier = _LLM_JUDGE_TIERS.get(url, MatchTier.green)
                return MatchVerdict(tier=tier, matched=[], missing=[], summary="ok")
        return MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok")


def test_integration_classify_judge_render_write_mark(tmp_path: Path) -> None:
    """Happy path: 6 stubs, 1 prefilter-dropped, 1 classifier-dropped, 4 written."""
    seen_path = tmp_path / ".seen.json"
    results_path = tmp_path / "current.md"
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
        sources='[SourceEntry(parser_type="stub")]',
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
    """All classify_relevance calls complete before any judge_match call."""
    call_log: list[str] = []

    class _InstrumentedExtractor:
        def prewarm(self) -> None:
            pass

        def classify_relevance(
            self, language: str, title: str, raw_description: str
        ) -> RelevanceVerdict:
            call_log.append("classify")
            return RelevanceVerdict(in_domain=True)

        def judge_match(self, language: str, raw_description: str) -> MatchVerdict:
            call_log.append("judge")
            return MatchVerdict(
                tier=MatchTier.green, matched=[], missing=[], summary="ok"
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
        sources='[SourceEntry(parser_type="stub")]',
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
        sources='[SourceEntry(parser_type="stub")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )


def test_extractor_error_on_classify_leaves_position_unseen(tmp_path: Path) -> None:
    """ExtractorError on classify_relevance: position NOT marked seen, errored increments, run completes."""
    seen_path = tmp_path / ".seen.json"

    ext = MagicMock()
    ext.prewarm.return_value = None
    # First position raises, second succeeds
    ext.classify_relevance.side_effect = [
        ExtractorError("classify boom"),
        RelevanceVerdict(in_domain=True),
    ]
    ext.judge_match.return_value = MatchVerdict(
        tier=MatchTier.green, matched=[], missing=[], summary="ok"
    )

    summary = run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        results_manager=_stub_results_manager(),
    )

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
    ext.classify_relevance.return_value = RelevanceVerdict(in_domain=True)
    # First judge raises, second succeeds
    ext.judge_match.side_effect = [
        ExtractorError("judge boom"),
        MatchVerdict(tier=MatchTier.green, matched=[], missing=[], summary="ok"),
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
            sources='[SourceEntry(parser_type="stub")]',
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
            sources='[SourceEntry(parser_type="stub")]',
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
            sources='[SourceEntry(parser_type="stub")]',
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
        sources='[SourceEntry(parser_type="dead"), SourceEntry(parser_type="healthy")]',
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
        sources='[SourceEntry(parser_type="stub")]',
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
        "classify_total_s=",
        "judge_calls=",
        "judge_total_s=",
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
        sources='[SourceEntry(parser_type="stub")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    class _CrashingExtractor:
        def prewarm(self) -> None:
            pass

        def classify_relevance(
            self, language: str, title: str, raw_description: str
        ) -> RelevanceVerdict:
            raise RuntimeError("unexpected crash escaping main path")

        def judge_match(
            self, language: str, raw_description: str
        ) -> MatchVerdict:  # pragma: no cover
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
            raise ExtractorUnreachableError("test: ollama unreachable")

    monkeypatch.setattr(
        "application_pipeline.orchestrator.OllamaExtractor",
        lambda *a, **kw: _FailingExtractor(),
    )

    from application_pipeline.__main__ import main

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1

    failures_dir = tmp_path / "results" / "failures"
    reports = list(failures_dir.glob("*.md"))
    assert len(reports) == 1, f"expected one failure report, got {reports}"

    body = reports[0].read_text(encoding="utf-8")
    assert "orchestrator" in body
    assert "startup failed" in body  # log tail captured before exception propagated


def test_results_write_stage_label_on_append_failure(tmp_path: Path) -> None:
    """ResultsFileError in step 12 → _stage_out set to 'results_write'."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="stub")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )
    crashing_rm = MagicMock()
    crashing_rm.ensure_initialized.return_value = None
    crashing_rm.next_position_number.return_value = 1
    crashing_rm.append.side_effect = ResultsFileError("disk full")

    stage_out: list[str] = ["orchestrator"]
    with pytest.raises(ResultsFileError):
        run(
            config_path,
            extractor=_FakeExtractor(),
            parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
            results_manager=crashing_rm,
            _stage_out=stage_out,
        )

    assert stage_out[0] == "results_write"
