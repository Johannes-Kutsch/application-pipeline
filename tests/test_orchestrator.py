from __future__ import annotations

import ast
import json
import re
import textwrap
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline import dedup as dedup_module
from application_pipeline.config import ConfigError
from application_pipeline.dedup import DedupStoreError
from application_pipeline.llm import (
    CallUsage,
    ClassifyItem,
    ExtractorError,
    ExtractorUnreachableError,
    JudgeCandidate,
    MatchVerdict,
    RelevanceVerdict,
)
from application_pipeline.llm.types import StructuredExtract
from application_pipeline.llm.claude_cli import ClaudeUsageLimitError
from application_pipeline.http import HttpParserFatalError, HttpStubNotRetryableError
from application_pipeline.orchestrator import RunSummary, run
from application_pipeline.parsers import (
    ExternalRedirect,
    Parser,
    ParserQuery,
    Position,
    PositionStub,
)
from application_pipeline.parsers.errors import ParserError
from application_pipeline.parsers.types import City, Remote
from application_pipeline.prompts import PromptError
from application_pipeline.results import ResultsFileError


class _StubParserBase:
    """Base for all test stub parsers; absorbs the run_log kwarg the orchestrator injects."""

    def __init__(self, **_: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write_config(
    tmp_path: Path,
    *,
    sources: str = '[SourceEntry(parser_type="bundesagentur_api")]',
    with_user_info_files: bool = True,
    keywords: str = '["python"]',
    locations: str = '["Hamburg"]',
    include_remote: bool = True,
    negative_keywords: str = "[]",
) -> Path:
    """Write a minimal valid config.py and a user-info dir into tmp_path."""
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
        """),
        encoding="utf-8",
    )
    (tmp_path / "layout.py").write_text(
        "PLACEHOLDER_GROUPS = {}\n"
        'CARD_TEMPLATE = "# {rank} \xb7 {title}\\n\\n{summary}\\n\\n---\\n<{url}>\\n"\n',
        encoding="utf-8",
    )
    user_info_dir = tmp_path / "user-info"
    user_info_dir.mkdir(exist_ok=True)
    if with_user_info_files:
        triage_dir = user_info_dir / "triage-profile"
        triage_dir.mkdir(exist_ok=True)
        (triage_dir / "self-description.md").write_text("dev background\n")
        (triage_dir / "domain-fit.md").write_text("ML roles\n")
        (triage_dir / "match-criteria.md").write_text("Hamburg, remote\n")
        kws: list[str] = ast.literal_eval(keywords)
        nkws: list[str] = ast.literal_eval(negative_keywords)
        st_dir = user_info_dir / "search-terms"
        st_dir.mkdir(exist_ok=True)
        (st_dir / "keywords.md").write_text(
            "\n".join(f"- {kw}" for kw in kws) + "\n", encoding="utf-8"
        )
        (st_dir / "skills.md").write_text("- django\n", encoding="utf-8")
        (st_dir / "negative-keywords.md").write_text(
            "\n".join(f"- {nk}" for nk in nkws) + "\n", encoding="utf-8"
        )
    return config_path


_ZERO_USAGE = CallUsage(
    input_tokens=0, output_tokens=0, cache_read_tokens=0, cost_usd=0.0, duration_s=0.0
)

_STUB_EXTRACT = StructuredExtract(
    seniority=None,
    work_model=None,
    contract_type=None,
    key_skills=[],
    key_responsibilities=[],
    must_have_requirements=[],
    notable_caveats="",
)


def _stub_extractor() -> MagicMock:
    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = lambda candidates: (
        [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="ok",
                rank=i + 1,
                id=c.id,
            )
            for i, c in enumerate(candidates[:5])
        ],
        _ZERO_USAGE,
    )
    return ext


def _read_all_results(results_dir: Path) -> str:
    """Read content from all dated daily result files."""
    parts = []
    if results_dir.exists():
        for f in sorted(results_dir.glob("????-??-??.md")):
            parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _wire_run_scope(dedup: MagicMock) -> None:
    """Configure a MagicMock dedup store so run_scope() yields the store itself."""

    @contextmanager
    def _run_scope():
        yield dedup

    dedup.run_scope.side_effect = _run_scope


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
    )

    assert isinstance(summary, RunSummary)
    assert summary.discovered == 0
    assert summary.skipped == 0
    assert summary.written == 0
    assert summary.classifier_dropped == 0
    assert summary.prefilter_dropped == 0
    assert summary.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# Fatal error paths
# ---------------------------------------------------------------------------


def test_config_error_propagates(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        run(tmp_path / "nonexistent.py")


def test_prompt_error_propagates(tmp_path: Path) -> None:
    # user-info dir exists but files are missing → PromptError on load_prompts
    config_path = _write_config(tmp_path, with_user_info_files=False)

    with pytest.raises(PromptError):
        run(
            config_path,
            # extractor=None so load_prompts() is called
            dedup_store=MagicMock(),
        )


def test_dedup_store_error_propagates(tmp_path: Path) -> None:
    (tmp_path / ".seen.json").write_text("not-valid-json", encoding="utf-8")
    config_path = _write_config(tmp_path)

    with pytest.raises(DedupStoreError):
        run(
            config_path,
            extractor=_stub_extractor(),
            # dedup_store=None so the store is loaded from seen_store_path
        )


def test_results_file_error_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_config(tmp_path)

    def _raise(_path: Path) -> None:
        raise ResultsFileError("cannot write")

    monkeypatch.setattr("application_pipeline.orchestrator.ensure_initialized", _raise)

    with pytest.raises(ResultsFileError):
        run(
            config_path,
            extractor=_stub_extractor(),
            dedup_store=MagicMock(),
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
    )

    assert isinstance(summary, RunSummary)
    assert summary.discovered == 0


# ---------------------------------------------------------------------------
# Integration: discover + dedup gating + enrich (no LLM)
# ---------------------------------------------------------------------------

_STUB_URLS = [f"https://stub.example/{i}" for i in range(6)]


class _StubParser(_StubParserBase):
    """Returns 3 stubs per discover() call (deterministic URLs), enriches trivially."""

    def __init__(self, **_: object) -> None:
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
    """2 keywords × 1 location, 3 stubs each → discovered==6, skipped==0, written==5 (capped by judge_top_n)."""
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
    )

    assert summary.discovered == 6
    assert summary.skipped == 0
    assert summary.written == 5  # judge_top_n caps at 5


def test_integration_all_skipped_when_preseeded(tmp_path: Path) -> None:
    """Pre-seed all 6 URLs → discovered==6, skipped==6, written==0."""
    seen_path = tmp_path / ".seen.json"
    seen_data = {
        url: {
            "company_lc": None,
            "title_lc": None,
            "location_lc": None,
            "status": "out_of_domain",
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
    )

    assert summary.discovered == 6
    assert summary.skipped == 6
    assert summary.written == 0


def test_integration_include_remote_emits_extra_discover_calls(tmp_path: Path) -> None:
    """include_remote=True adds one (keyword, None) call per keyword per source."""
    queries_received: list[ParserQuery] = []

    class _TrackingParser(_StubParserBase):
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
    )

    assert len(queries_received) == 2
    geo_calls = [q for q in queries_received if isinstance(q.location, City)]
    remote_calls = [q for q in queries_received if isinstance(q.location, Remote)]
    assert len(geo_calls) == 1
    assert len(remote_calls) == 1
    assert geo_calls[0].location == City("Hamburg")


# ---------------------------------------------------------------------------
# Integration: dedup counter breakdown (issue #177)
# ---------------------------------------------------------------------------


def test_integration_dedup_counter_breakdown(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """2 url_hits + 1 tuple_hit + 4 misses → dedup_url_hits=2 dedup_tuple_hits=1 dedup_misses=4 in RunSummary and run complete: log."""
    import logging

    _DEDUP_URLS = [f"https://dedup.example/{i}" for i in range(7)]

    class _SevenStubParser(_StubParserBase):
        def __enter__(self) -> "_SevenStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=_DEDUP_URLS[i], title=f"Job {i}", source="stub")
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
    _wire_run_scope(dedup)

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
# Integration: in-run dedup (issue #225)
# ---------------------------------------------------------------------------


def test_in_run_dedup_same_url_across_two_queries(tmp_path: Path) -> None:
    """Same URL yielded by two different ParserQuerys → second yield is run_hit, enrich() called once."""
    enrich_calls: list[str] = []
    duplicate_url = "https://dup.example/job"

    class _DupParser(_StubParserBase):
        def __enter__(self) -> "_DupParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=duplicate_url, title="Job", source="stub")]

        def enrich(self, stub: PositionStub) -> Position:
            enrich_calls.append(stub.url)
            return Position(stub=stub, raw_description="good description")

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python", "django"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _DupParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.dedup_run_hits == 1
    assert enrich_calls.count(duplicate_url) == 1


def test_consecutive_url_hits_never_trigger_skip_and_end_query(tmp_path: Path) -> None:
    """A long run of url_hits never causes the orchestrator to emit SKIP_AND_END_QUERY.

    100 consecutive url_hits (well above any former threshold) must all be consumed.
    """
    consumed: list[int] = [0]
    all_stubs = [
        PositionStub(
            url=f"https://noearlystop.example/{i}", title=f"Job {i}", source="stub"
        )
        for i in range(100)
    ]

    class _HitOnlyParser(_StubParserBase):
        def __enter__(self) -> "_HitOnlyParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            for stub in all_stubs:
                consumed[0] += 1
                yield stub

        def enrich(self, stub: PositionStub) -> Position:  # pragma: no cover
            raise NotImplementedError

    dedup = MagicMock()
    dedup.is_seen.return_value = "url_hit"
    _wire_run_scope(dedup)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _HitOnlyParser,  # type: ignore[return-value]
        dedup_store=dedup,
    )

    assert consumed[0] == 100
    assert summary.discovered == 100
    assert summary.dedup_url_hits == 100
    assert summary.skipped == 100


def test_off_domain_leading_stubs_do_not_hide_unseen_trailing_stub(
    tmp_path: Path,
) -> None:
    """A leading run of url_hit stubs does not prevent the trailing unseen stub from being enriched.

    AC4: A run whose parser yields N url_hit stubs followed by one unseen stub
    reaches and enriches the unseen stub.
    """
    enrich_calls: list[str] = []
    unseen_url = "https://trailing.example/new"
    all_stubs = [
        PositionStub(
            url=f"https://trailing.example/old/{i}", title=f"Old {i}", source="stub"
        )
        for i in range(80)
    ] + [PositionStub(url=unseen_url, title="New Job", source="stub")]

    class _TrailingParser(_StubParserBase):
        def __enter__(self) -> "_TrailingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return all_stubs

        def enrich(self, stub: PositionStub) -> Position:
            enrich_calls.append(stub.url)
            return Position(stub=stub, raw_description="new and relevant")

    dedup = MagicMock()
    dedup.is_seen.side_effect = ["url_hit"] * 80 + ["miss"]
    _wire_run_scope(dedup)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _TrailingParser,  # type: ignore[return-value]
        dedup_store=dedup,
    )

    assert unseen_url in enrich_calls, "trailing unseen stub must be enriched"
    assert summary.discovered == 81
    assert summary.dedup_url_hits == 80
    assert summary.dedup_misses == 1


def test_in_run_dedup_run_hits_in_log_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """dedup_run_hits=N appears in the 'run complete:' log line when in-run dupes exist."""
    import logging

    dup_url = "https://log.example/dup"

    class _DupParser(_StubParserBase):
        def __enter__(self) -> "_DupParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=dup_url, title="Dup", source="stub")]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="good description")

    with caplog.at_level(logging.INFO, logger="application_pipeline.orchestrator"):
        run(
            _write_config(
                tmp_path,
                sources='[SourceEntry(parser_type="bundesagentur_api")]',
                keywords='["python", "django"]',
                locations='["Hamburg"]',
                include_remote=False,
            ),
            extractor=_stub_extractor(),
            parser_registry=lambda _: _DupParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        )

    run_complete = next(
        r.getMessage() for r in caplog.records if "run complete:" in r.getMessage()
    )
    assert "dedup_run_hits=1" in run_complete


def test_in_run_set_is_fresh_per_run_invocation(tmp_path: Path) -> None:
    """A second run() call starts with an empty in-run set; the URL seen in run 1 is not a run_hit in run 2."""
    dup_url = "https://fresh.example/job"

    class _OneStubParser(_StubParserBase):
        def __enter__(self) -> "_OneStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=dup_url, title="Job", source="stub")]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="good description")

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    dedup = MagicMock()
    # Both runs: is_seen returns "miss" (in-run set does not carry across runs)
    dedup.is_seen.return_value = "miss"
    _wire_run_scope(dedup)

    summary1 = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value]
        dedup_store=dedup,
    )
    summary2 = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value]
        dedup_store=dedup,
    )

    # Each run sees the URL as a miss (in-run set starts fresh); no run_hits in either run
    assert summary1.dedup_run_hits == 0
    assert summary2.dedup_run_hits == 0
    assert summary1.dedup_misses == 1
    assert summary2.dedup_misses == 1


# ---------------------------------------------------------------------------
# Integration: classify + judge + render + write + mark (slice 5 / issue #109)
# ---------------------------------------------------------------------------

_STUB_URLS_LLM = [f"https://stub.example/llm/{i}" for i in range(6)]
_PF_REJECTED_LLM_URL = _STUB_URLS_LLM[0]  # prefilter rejects: "excluded" in title
_CLS_REJECTED_LLM_URL = _STUB_URLS_LLM[1]  # classifier rejects: title "Job 1"


class _LLMStubParser(_StubParserBase):
    """6 stubs; stub 0 has 'excluded' in title so Pre-Filter drops it."""

    def __enter__(self) -> "_LLMStubParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return [
            PositionStub(
                url=_STUB_URLS_LLM[i],
                title="Excluded Role" if i == 0 else f"Job {i}",
                source="stub",
            )
            for i in range(6)
        ]

    def enrich(self, stub: PositionStub) -> Position:
        # Encode URL index in description so judge can return the right tier
        idx = _STUB_URLS_LLM.index(stub.url)
        return Position(stub=stub, raw_description=f"description for job {idx}")


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

    def classify_relevance(
        self, item: ClassifyItem
    ) -> tuple[RelevanceVerdict, CallUsage]:
        in_domain = item.title != "Job 1"
        return (
            RelevanceVerdict(
                in_domain=in_domain,
                extract=_STUB_EXTRACT if in_domain else None,
            ),
            _FAKE_CLASSIFY_USAGE,
        )

    def judge_top_n(
        self, candidates: list[JudgeCandidate]
    ) -> tuple[list[MatchVerdict], CallUsage]:
        verdicts = []
        for i, c in enumerate(candidates[:5]):
            verdicts.append(
                MatchVerdict(matched=[], missing=[], summary="ok", rank=i + 1, id=c.id)
            )
        return verdicts, _FAKE_JUDGE_USAGE


def test_integration_classify_judge_render_write_mark(tmp_path: Path) -> None:
    """Happy path: 6 stubs, 1 prefilter-dropped, 1 classifier-dropped, 4 written."""
    seen_path = tmp_path / ".seen.json"
    results_dir = tmp_path / "results"
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
    )

    assert summary.discovered == 6
    assert summary.skipped == 0
    assert summary.prefilter_dropped == 1
    assert summary.classifier_dropped == 1
    assert summary.written == 4

    # .seen.json: 2 out_of_domain, 4 selected_by_judge
    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    out_of_domain = [
        url for url, rec in seen_data.items() if rec["status"] == "out_of_domain"
    ]
    selected = [
        url for url, rec in seen_data.items() if rec["status"] == "selected_by_judge"
    ]
    assert len(out_of_domain) == 2
    assert len(selected) == 4
    assert _PF_REJECTED_LLM_URL in out_of_domain
    assert _CLS_REJECTED_LLM_URL in out_of_domain

    # 4 card H1s in the daily results file
    content = _read_all_results(results_dir)
    cards = re.findall(r"^# .+ · .+", content, re.MULTILINE)
    assert len(cards) == 4


def test_integration_dedup_skip_rerun(tmp_path: Path) -> None:
    """Second run on same tmp_path → all 6 skipped, tier files unchanged."""
    seen_path = tmp_path / ".seen.json"
    results_dir = tmp_path / "results"
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
        )

    first = _make_run()
    assert first.written == 4

    numbers_after_first = re.findall(
        r"^## (\d+)\.", _read_all_results(results_dir), re.MULTILINE
    )

    second = _make_run()
    assert second.discovered == 6
    assert second.skipped == 6
    assert second.prefilter_dropped == 0
    assert second.classifier_dropped == 0
    assert second.written == 0

    # No new position entries added on second run
    numbers_after_second = re.findall(
        r"^## (\d+)\.", _read_all_results(results_dir), re.MULTILINE
    )
    assert numbers_after_second == numbers_after_first


def test_classify_precedes_judge(tmp_path: Path) -> None:
    """All classify_relevance calls complete before any judge_top_n call."""
    call_log: list[str] = []

    class _InstrumentedExtractor:
        def classify_relevance(
            self, item: ClassifyItem
        ) -> tuple[RelevanceVerdict, CallUsage]:
            call_log.append("classify")
            return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            call_log.append("judge_top_n")
            return [
                MatchVerdict(
                    matched=[],
                    missing=[],
                    summary="ok",
                    rank=i + 1,
                    id=c.id,
                )
                for i, c in enumerate(candidates[:5])
            ], _ZERO_USAGE

    class _MultiStubParser(_StubParserBase):
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
    )

    assert call_log.count("classify") == 5
    assert call_log.count("judge_top_n") == 1
    assert call_log.count("judge_match") == 0
    # All classify calls before the judge_top_n call
    last_classify = max(i for i, c in enumerate(call_log) if c == "classify")
    first_judge = min(i for i, c in enumerate(call_log) if c == "judge_top_n")
    assert last_classify < first_judge, (
        f"classify and judge_top_n calls interleaved: {call_log}"
    )


# ---------------------------------------------------------------------------
# Error paths (issue #110)
# ---------------------------------------------------------------------------

_ERR_URLS = [f"https://stub.example/err/{i}" for i in range(4)]


class _TwoStubParser(_StubParserBase):
    """Yields 2 stubs with fixed URLs; enriches trivially."""

    def __enter__(self) -> "_TwoStubParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return [
            PositionStub(url=_ERR_URLS[i], title=f"Job {i}", source="stub")
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


def test_extractor_error_on_classify_leaves_position_unseen(tmp_path: Path) -> None:
    """ExtractorError on classify_relevance: the failing position is not marked seen; run continues."""
    seen_path = tmp_path / ".seen.json"

    call_count = [0]

    def _classify_side_effect(
        item: ClassifyItem,
    ) -> tuple[RelevanceVerdict, CallUsage]:
        call_count[0] += 1
        if call_count[0] == 1:
            raise ExtractorError("classify boom")
        return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

    ext = MagicMock()
    ext.classify_relevance.side_effect = _classify_side_effect
    ext.judge_top_n.side_effect = lambda candidates: (
        [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="ok",
                rank=i + 1,
                id=c.id,
            )
            for i, c in enumerate(candidates[:5])
        ],
        _ZERO_USAGE,
    )

    summary = run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    # First position errored, second succeeded (1 position written)
    assert summary.errored == 1
    assert summary.written == 1

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    # First position must NOT be in seen store (left un-seen for retry)
    assert _ERR_URLS[0] not in seen_data


def test_extractor_error_on_judge_leaves_status_in_domain(tmp_path: Path) -> None:
    """ExtractorError on judge_top_n: all positions stay in_domain (not selected_by_judge), no daily file written."""
    seen_path = tmp_path / ".seen.json"

    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = ExtractorError("judge boom")

    summary = run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.written == 0

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_ERR_URLS[0]]["status"] == "in_domain"
    assert seen_data[_ERR_URLS[1]]["status"] == "in_domain"


def test_parser_error_on_enrich_marks_enrich_failed(tmp_path: Path) -> None:
    """ParserError from enrich: stub marked enrich_failed, enrich_failed increments, other stubs proceed."""
    seen_path = tmp_path / ".seen.json"

    class _EnrichFailParser(_StubParserBase):
        def __enter__(self) -> "_EnrichFailParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=_ERR_URLS[i], title=f"Job {i}", source="stub")
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
    )

    assert summary.enrich_failed == 1
    assert summary.written == 2

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_ERR_URLS[1]]["status"] == "enrich_failed"


def test_per_stub_http_error_on_enrich_increments_enrich_failed_and_continues(
    tmp_path: Path,
) -> None:
    """enrich() raises ParserError (from HttpStubNotRetryableError path) → enrich_failed, parser thread continues."""
    seen_path = tmp_path / ".seen.json"

    class _PerStubHttpParser(_StubParserBase):
        def __enter__(self) -> "_PerStubHttpParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=_ERR_URLS[i], title=f"Job {i}", source="stub")
                for i in range(3)
            ]

        def enrich(self, stub: PositionStub) -> Position:
            if stub.url == _ERR_URLS[1]:
                exc = HttpStubNotRetryableError("not found: stub_url")
                raise ParserError("myjobboard: not found: stub_url") from exc
            return Position(stub=stub, raw_description="ok")

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _PerStubHttpParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.enrich_failed == 1
    assert summary.parsers_dead == 0
    assert summary.written == 2
    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_ERR_URLS[1]]["status"] == "enrich_failed"


def test_parser_fatal_http_error_on_enrich_marks_parser_dead_surviving_parsers_continue(
    tmp_path: Path,
) -> None:
    """enrich() raises HttpParserFatalError → parsers_dead increments, surviving parsers complete."""

    class _FatalHttpParser(_StubParserBase):
        def __enter__(self) -> "_FatalHttpParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=_ERR_URLS[0], title="Job 0", source="fatal")]

        def enrich(self, stub: PositionStub) -> Position:
            raise HttpParserFatalError("auth: stub_url status=401")

    class _HealthyParser(_StubParserBase):
        def __enter__(self) -> "_HealthyParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://ok.example/0", title="Job ok", source="healthy"
                )
            ]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="ok")

    def _registry(parser_type: str) -> type[Parser] | None:
        if parser_type == "fatal":
            return _FatalHttpParser  # type: ignore[return-value]
        if parser_type == "healthy":
            return _HealthyParser  # type: ignore[return-value]
        return None

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api"), SourceEntry(parser_type="fatal"), SourceEntry(parser_type="healthy")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=_registry,
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.parsers_dead == 1
    assert summary.written == 1


def test_external_redirect_marks_seen_and_increments_counter(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ExternalRedirect: stub marked external_redirect, external_redirects increments, enrich_failed unchanged, event in parser log, no WARNING."""
    import logging

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

    seen_path = tmp_path / ".seen.json"

    class _RedirectParser(_StubParserBase):
        def __enter__(self) -> "_RedirectParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=_ERR_URLS[i], title=f"Job {i}", source="stub")
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
            run_log=run_log,
        )

    assert summary.external_redirects == 1
    assert summary.enrich_failed == 0
    assert summary.written == 1

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_ERR_URLS[0]]["status"] == "external_redirect"

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records == [], f"unexpected WARNING(s): {warning_records}"

    events_content = (logs_dir / "parser_bundesagentur_api.events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "external_redirect" in events_content
    assert "https://external.example/job" in events_content


def test_external_redirect_event_row_includes_skipped_true(tmp_path: Path) -> None:
    """external_redirect event row must include skipped=True per ADR-0028."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

    class _RedirectParser(_StubParserBase):
        def __enter__(self) -> "_RedirectParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://example.com/job/1", title="Job 1", source="stub"
                )
            ]

        def enrich(self, stub: PositionStub) -> Position | ExternalRedirect:
            return ExternalRedirect(
                stub=stub, outbound_url="https://external.example/job"
            )

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _RedirectParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events = [
        json.loads(line)
        for line in (logs_dir / "parser_bundesagentur_api.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    redirect_rows = [r for r in events if r.get("event") == "external_redirect"]
    assert len(redirect_rows) == 1
    assert redirect_rows[0]["skipped"] is True


def test_parser_error_mid_discover_processes_yielded_stubs(tmp_path: Path) -> None:
    """ParserError mid-discover: already-yielded stubs processed, run advances to next combination."""
    seen_path = tmp_path / ".seen.json"

    class _MidDiscoverFailParser(_StubParserBase):
        def __enter__(self) -> "_MidDiscoverFailParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            # Yield 3 stubs then raise ParserError
            for i in range(3):
                yield PositionStub(url=_ERR_URLS[i], title=f"Job {i}", source="stub")
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
    )

    assert summary.discovered == 3
    assert summary.written == 3


# ---------------------------------------------------------------------------
# judge_pending: classify→judge boundary idempotency (issue #289)
# ---------------------------------------------------------------------------

_RESUME_URL = "https://resume.example/job/0"


class _OneStubParser(_StubParserBase):
    """Single-stub parser used by the judge_pending tests."""

    def __enter__(self) -> "_OneStubParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return [PositionStub(url=_RESUME_URL, title="ML Engineer", source="stub")]

    def enrich(self, stub: PositionStub) -> Position:
        return Position(stub=stub, raw_description="good ml job description")


def _one_stub_config(tmp_path: Path) -> Path:
    return _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )


def test_judge_failure_leaves_status_in_domain(tmp_path: Path) -> None:
    """After classify succeeds and judge_top_n raises ExtractorError, seen store has in_domain."""
    seen_path = tmp_path / ".seen.json"

    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = ExtractorError("judge boom")

    run(
        _one_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_RESUME_URL]["status"] == "in_domain"


def test_judge_pending_bypasses_classify_on_rerun(tmp_path: Path) -> None:
    """On rerun, an in_domain URL is enriched and judged without classify being called."""
    seen_path = tmp_path / ".seen.json"
    classify_calls: list[object] = []
    enrich_calls: list[str] = []

    class _TrackingParser(_StubParserBase):
        def __enter__(self) -> "_TrackingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=_RESUME_URL, title="ML Engineer", source="stub")]

        def enrich(self, stub: PositionStub) -> Position:
            enrich_calls.append(stub.url)
            return Position(stub=stub, raw_description="fresh description from rerun")

    class _TrackingExtractor:
        def classify_relevance(
            self, item: ClassifyItem
        ) -> tuple[RelevanceVerdict, CallUsage]:
            classify_calls.append(item)
            return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            return [
                MatchVerdict(
                    matched=[],
                    missing=[],
                    summary="ok",
                    rank=i + 1,
                    id=c.id,
                )
                for i, c in enumerate(candidates[:5])
            ], _ZERO_USAGE

    seen_path.write_text(
        json.dumps(
            {
                _RESUME_URL: {
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "in_domain",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    # extracts.json must exist so build_candidates can retrieve the extract for judge_pending URLs
    _stub_extract_record: dict[str, object] = {
        "seniority": None,
        "work_model": None,
        "contract_type": None,
        "key_skills": [],
        "key_responsibilities": [],
        "must_have_requirements": [],
        "notable_caveats": "",
    }
    (tmp_path / "extracts.json").write_text(
        json.dumps({_RESUME_URL: _stub_extract_record}),
        encoding="utf-8",
    )

    summary = run(
        _one_stub_config(tmp_path),
        extractor=_TrackingExtractor(),
        parser_registry=lambda _: _TrackingParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    assert classify_calls == [], "classify must not be called for judge_pending stubs"
    assert _RESUME_URL in enrich_calls, "enrich must be called for judge_pending stub"
    assert summary.written == 1, "judge_pending stub must reach judge and be written"


def test_judge_pending_success_transitions_to_selected_by_judge(tmp_path: Path) -> None:
    """On rerun, if judge succeeds the URL transitions from in_domain to selected_by_judge."""
    seen_path = tmp_path / ".seen.json"

    seen_path.write_text(
        json.dumps(
            {
                _RESUME_URL: {
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "in_domain",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    # extracts.json must exist so build_candidates produces a JudgeCandidate for the judge_pending URL
    _ser: dict[str, object] = {
        "seniority": None,
        "work_model": None,
        "contract_type": None,
        "key_skills": [],
        "key_responsibilities": [],
        "must_have_requirements": [],
        "notable_caveats": "",
    }
    (tmp_path / "extracts.json").write_text(
        json.dumps({_RESUME_URL: _ser}), encoding="utf-8"
    )

    run(
        _one_stub_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_RESUME_URL]["status"] == "selected_by_judge"


def test_judge_pending_failure_stays_in_domain(tmp_path: Path) -> None:
    """On rerun, if judge fails again the URL stays in_domain for the next run."""
    seen_path = tmp_path / ".seen.json"

    seen_path.write_text(
        json.dumps(
            {
                _RESUME_URL: {
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "in_domain",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    # extracts.json so build_candidates finds the candidate and judge_top_n is actually called
    _ser: dict[str, object] = {
        "seniority": None,
        "work_model": None,
        "contract_type": None,
        "key_skills": [],
        "key_responsibilities": [],
        "must_have_requirements": [],
        "notable_caveats": "",
    }
    (tmp_path / "extracts.json").write_text(
        json.dumps({_RESUME_URL: _ser}), encoding="utf-8"
    )

    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = ExtractorError("judge boom again")

    run(
        _one_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_RESUME_URL]["status"] == "in_domain"


def test_judge_pending_enrich_re_fetches_fresh_page(tmp_path: Path) -> None:
    """The dedup store does not cache raw_description; enrich is called fresh on rerun."""
    seen_path = tmp_path / ".seen.json"
    enrich_descriptions: list[str] = []
    judge_candidate_ids: list[str] = []

    class _FreshDescParser(_StubParserBase):
        def __enter__(self) -> "_FreshDescParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=_RESUME_URL, title="ML Engineer", source="stub")]

        def enrich(self, stub: PositionStub) -> Position:
            desc = "fresh description fetched on rerun"
            enrich_descriptions.append(desc)
            return Position(stub=stub, raw_description=desc)

    class _CapturingExtractor:
        def classify_relevance(
            self, item: ClassifyItem
        ) -> tuple[RelevanceVerdict, CallUsage]:
            return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            for c in candidates:
                judge_candidate_ids.append(c.id)
            return [
                MatchVerdict(
                    matched=[],
                    missing=[],
                    summary="ok",
                    rank=i + 1,
                    id=c.id,
                )
                for i, c in enumerate(candidates[:5])
            ], _ZERO_USAGE

    seen_path.write_text(
        json.dumps(
            {
                _RESUME_URL: {
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "in_domain",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    # extracts.json must exist so build_candidates can retrieve the extract for judge_pending URLs
    _stub_extract_record: dict[str, object] = {
        "seniority": None,
        "work_model": None,
        "contract_type": None,
        "key_skills": [],
        "key_responsibilities": [],
        "must_have_requirements": [],
        "notable_caveats": "",
    }
    (tmp_path / "extracts.json").write_text(
        json.dumps({_RESUME_URL: _stub_extract_record}),
        encoding="utf-8",
    )

    run(
        _one_stub_config(tmp_path),
        extractor=_CapturingExtractor(),
        parser_registry=lambda _: _FreshDescParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    assert enrich_descriptions == ["fresh description fetched on rerun"], (
        "enrich must be called fresh on rerun"
    )
    assert _RESUME_URL in judge_candidate_ids, (
        "judge_top_n must be called with the freshly-enriched position as a candidate"
    )


def test_judge_pending_enrich_failure_marks_enrich_failed(tmp_path: Path) -> None:
    """If enrich fails on a judge_pending stub, it is marked enrich_failed."""
    seen_path = tmp_path / ".seen.json"

    class _EnrichFailParser(_StubParserBase):
        def __enter__(self) -> "_EnrichFailParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=_RESUME_URL, title="ML Engineer", source="stub")]

        def enrich(self, stub: PositionStub) -> Position:
            raise ParserError("enrich failed on resume")

    seen_path.write_text(
        json.dumps(
            {
                _RESUME_URL: {
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "in_domain",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )

    summary = run(
        _one_stub_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _EnrichFailParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.enrich_failed == 1
    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_RESUME_URL]["status"] == "enrich_failed"


def test_judge_pending_appears_in_run_complete_event(tmp_path: Path) -> None:
    """judge_resumed=N appears in run_complete event when stubs took the judge_pending path."""
    import application_pipeline.parser_log as parser_log

    seen_path = tmp_path / ".seen.json"
    logs_dir = tmp_path / "logs"
    run_log = parser_log.RunLog(logs_dir)

    seen_path.write_text(
        json.dumps(
            {
                _RESUME_URL: {
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "in_domain",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    _ser: dict[str, object] = {
        "seniority": None,
        "work_model": None,
        "contract_type": None,
        "key_skills": [],
        "key_responsibilities": [],
        "must_have_requirements": [],
        "notable_caveats": "",
    }
    (tmp_path / "extracts.json").write_text(
        json.dumps({_RESUME_URL: _ser}), encoding="utf-8"
    )

    summary = run(
        _one_stub_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        run_log=run_log,
    )

    assert summary.written == 1, "judge_pending stub must be written"


def test_judge_pending_judge_failure_stays_in_domain_on_rerun(
    tmp_path: Path,
) -> None:
    """On rerun, if judge_top_n fails the resumed stub stays in_domain."""
    seen_path = tmp_path / ".seen.json"

    seen_path.write_text(
        json.dumps(
            {
                _RESUME_URL: {
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "in_domain",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    _ser: dict[str, object] = {
        "seniority": None,
        "work_model": None,
        "contract_type": None,
        "key_skills": [],
        "key_responsibilities": [],
        "must_have_requirements": [],
        "notable_caveats": "",
    }
    (tmp_path / "extracts.json").write_text(
        json.dumps({_RESUME_URL: _ser}), encoding="utf-8"
    )

    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = ExtractorError("judge boom")

    run(
        _one_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_RESUME_URL]["status"] == "in_domain"


# ---------------------------------------------------------------------------
# Threading: PARSER_DEAD (issue #112)
# ---------------------------------------------------------------------------


def test_parser_thread_dead_run_completes(tmp_path: Path) -> None:
    """Uncaught exception in parser thread → parsers_dead==1, run completes (no hang)."""

    class _DeadParser(_StubParserBase):
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
    )

    assert summary.parsers_dead == 1
    assert summary.discovered == 0
    assert summary.written == 0


def test_parser_thread_dead_surviving_parsers_continue(tmp_path: Path) -> None:
    """One dead parser + one healthy parser → dead counted, healthy stubs written."""

    class _DeadParser(_StubParserBase):
        def __enter__(self) -> "_DeadParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            raise RuntimeError("boom")
            yield  # pragma: no cover

        def enrich(self, stub: PositionStub) -> Position:  # pragma: no cover
            raise NotImplementedError

    class _HealthyParser(_StubParserBase):
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
    )

    assert summary.parsers_dead == 1
    assert summary.discovered == 1
    assert summary.written == 1


def test_append_failure_exits_nonzero_position_not_marked_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ResultsFileError from append: run raises (non-zero exit), position NOT marked seen."""
    seen_path = tmp_path / ".seen.json"

    def _raise(_path: Path, _text: str) -> None:
        raise ResultsFileError("disk full")

    monkeypatch.setattr("application_pipeline.orchestrator.append", _raise)

    with pytest.raises(ResultsFileError):
        run(
            _two_stub_config(tmp_path),
            extractor=_stub_extractor(),
            parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(seen_path),
        )

    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    # No position must be marked kept
    kept = [
        url
        for url, rec in seen_data.items()
        if rec.get("status") == "selected_by_judge"
    ]
    assert kept == []


# ---------------------------------------------------------------------------
# run_complete event (issue #116 / #390)
# ---------------------------------------------------------------------------


def test_run_complete_event_logged_to_pipeline_orchestrator_events(
    tmp_path: Path,
) -> None:
    """Successful run writes a run_complete event to pipeline_orchestrator.events.jsonl."""
    import application_pipeline.parser_log as parser_log

    seen_path = tmp_path / ".seen.json"
    logs_dir = tmp_path / "logs"
    run_log = parser_log.RunLog(logs_dir)
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
        run_log=run_log,
    )

    events_file = logs_dir / "pipeline_orchestrator.events.jsonl"
    assert events_file.exists(), "pipeline_orchestrator.events.jsonl must be written"
    rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    run_complete_rows = [r for r in rows if r.get("event") == "run_complete"]
    assert len(run_complete_rows) == 1
    row = run_complete_rows[0]
    for key in (
        "classify_calls",
        "dedup_url_hits",
        "dedup_tuple_hits",
        "dedup_run_hits",
        "dedup_misses",
        "elapsed_s",
    ):
        assert key in row, f"key {key!r} missing from run_complete event"


def test_run_complete_event_carries_dedup_run_hits(tmp_path: Path) -> None:
    """run_complete event includes dedup_run_hits=1 when in-run dupes are present."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "logs"
    run_log = parser_log.RunLog(logs_dir)
    dup_url = "https://divider.example/dup"

    class _DupParser(_StubParserBase):
        def __enter__(self) -> "_DupParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=dup_url, title="Dup", source="stub")]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="good description")

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python", "django"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _DupParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_file = logs_dir / "pipeline_orchestrator.events.jsonl"
    rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    run_complete_rows = [r for r in rows if r.get("event") == "run_complete"]
    assert len(run_complete_rows) == 1
    assert run_complete_rows[0]["dedup_run_hits"] == 1


def test_crashed_run_does_not_write_daily_file(tmp_path: Path) -> None:
    """An exception escaping the main run path does not produce a daily results file."""
    from datetime import datetime, timezone

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    today = datetime.now(timezone.utc).date().isoformat()
    results_dir = tmp_path / "results"

    class _CrashingExtractor:
        def classify_relevance(
            self, item: ClassifyItem
        ) -> tuple[RelevanceVerdict, CallUsage]:
            raise RuntimeError("unexpected crash escaping main path")

        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:  # pragma: no cover
            raise NotImplementedError

    with pytest.raises(RuntimeError):
        run(
            config_path,
            extractor=_CrashingExtractor(),
            parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        )

    # Daily file should not contain any card content (it's initialized empty)
    dated_file = results_dir / f"{today}.md"
    if dated_file.exists():
        content = dated_file.read_text(encoding="utf-8")
        assert re.findall(r"^# .+ · .+", content, re.MULTILINE) == [], (
            "no cards must be written when run crashes"
        )


# ---------------------------------------------------------------------------
# Failure Report (issue #117)
# ---------------------------------------------------------------------------


def test_fatal_error_writes_failure_report_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DedupStoreError at startup → failure report written, stage=orchestrator, exit 1."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "application-pipeline").mkdir()
    _write_config(tmp_path / "application-pipeline")
    monkeypatch.setattr("sys.argv", ["app", "run"])

    def _raise(*a: object, **kw: object) -> None:
        raise DedupStoreError("test: store unavailable")

    monkeypatch.setattr(
        "application_pipeline.orchestrator.dedup_module.load",
        _raise,
    )

    from application_pipeline.__main__ import main

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1

    failures_dir = tmp_path / "application-pipeline" / "failures"
    reports = list(failures_dir.glob("*.md"))
    assert len(reports) == 1, f"expected one failure report, got {reports}"

    body = reports[0].read_text(encoding="utf-8")
    assert "orchestrator" in body
    assert "startup failed" in body  # log tail captured before exception propagated


def test_results_write_error_propagates_from_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ResultsFileError from append in judge worker propagates from run()."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )

    def _raise(_path: Path, _text: str) -> None:
        raise ResultsFileError("disk full")

    monkeypatch.setattr("application_pipeline.orchestrator.append", _raise)

    with pytest.raises(ResultsFileError):
        run(
            config_path,
            extractor=_FakeExtractor(),
            parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        )


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
    run_log = parser_log.RunLog(logs_dir)

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
            run_log=run_log,
        )

    events_file = logs_dir / "parser_bundesagentur_api.events.jsonl"
    assert events_file.exists(), "events log file must be created"
    events_content = events_file.read_text(encoding="utf-8")
    assert "parser started" in events_content

    run_log_content = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert "SUMMARY OF SESSION" in run_log_content
    assert "discovered=" in run_log_content
    assert "duration=" in run_log_content

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

    class _NotServedParser(_StubParserBase):
        def __enter__(self) -> "_NotServedParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[NotServedQuery]:
            return [NotServedQuery()]

        def enrich(self, stub: PositionStub) -> Position:
            raise AssertionError("enrich must not be called")

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

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
            run_log=run_log,
        )

    # Events file must not contain not_served events
    events_file = logs_dir / "parser_bundesagentur_api.events.jsonl"
    if events_file.exists():
        assert "not_served" not in events_file.read_text(encoding="utf-8")

    # SUMMARY in run.log must contain not_served_queries=3
    run_log_content = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert "SUMMARY OF SESSION" in run_log_content
    assert "not_served_queries=3" in run_log_content

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
    run_log = parser_log.RunLog(logs_dir)

    _STUB_URLS = [
        "https://stub.example/0",
        "https://stub.example/1",
    ]

    class _ThreeEventParser(_StubParserBase):
        def __enter__(self) -> "_ThreeEventParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            yield PositionStub(url=_STUB_URLS[0], title="Job 0", source="stub")
            yield PositionStub(url=_STUB_URLS[1], title="Job 1", source="stub")
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
            run_log=run_log,
        )

    assert summary.enrich_failed == 1
    assert summary.external_redirects == 1
    assert summary.parsers_dead == 1

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records == [], f"unexpected WARNING/ERROR(s): {warning_records}"

    events_file = logs_dir / "parser_bundesagentur_api.events.jsonl"
    assert events_file.exists(), "events log file must be created"
    events_content = events_file.read_text(encoding="utf-8")
    assert "enrich_failed" in events_content
    assert "external_redirect" in events_content

    run_log_content = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert "traceback" in run_log_content
    assert "enrich_failed=1" in run_log_content
    assert "external_redirects=1" in run_log_content
    assert "parsers_dead=1" in run_log_content


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


class _WarnParser(_StubParserBase):
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
    run_log = parser_log.RunLog(logs_dir)

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
        run_log=run_log,
    )

    events_file = logs_dir / "parser_jobs_beim_staat_html.events.jsonl"
    assert events_file.exists()
    events_rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        "unparseable_date" in str(row.get("event", ""))
        and "INVALID_DATE" in str(row.get("event", ""))
        for row in events_rows
    ), "unparseable_date event with raw=INVALID_DATE must appear in events log"
    run_log_content = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert "unparseable_dates=1" in run_log_content
    assert summary.written == 1


def test_unparseable_date_warning_not_emitted_to_stderr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The unparseable_date warning must NOT appear in stderr (the old _log.info path)."""
    import logging

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

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
            run_log=run_log,
        )

    assert not any("unparseable_date" in record.message for record in caplog.records), (
        "unparseable_date must not appear in logging output"
    )


# ---------------------------------------------------------------------------
# Batched classify pipeline — new tests for issue #187
# ---------------------------------------------------------------------------


def _batch_size_config(tmp_path: Path, batch_size: int = 1) -> Path:
    # batch_size is ignored — solo calls per position, no batching
    return _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )


def test_four_positions_each_get_solo_classify_call(tmp_path: Path) -> None:
    """4 positions → classify_relevance called 4 times, once per position."""
    call_count = [0]

    def _classify(item: ClassifyItem) -> tuple[RelevanceVerdict, CallUsage]:
        call_count[0] += 1
        return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

    ext = MagicMock()
    ext.classify_relevance.side_effect = _classify
    ext.judge_top_n.side_effect = lambda candidates: (
        [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="ok",
                rank=i + 1,
                id=c.id,
            )
            for i, c in enumerate(candidates[:5])
        ],
        _ZERO_USAGE,
    )

    class _FourStubParser(_StubParserBase):
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
                )
                for i in range(4)
            ]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="good description")

    summary = run(
        _batch_size_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _FourStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.written == 4
    assert call_count[0] == 4


def test_parser_classify_overlap(tmp_path: Path) -> None:
    """First classify batch completes before parser finishes when parser is slow.

    Uses a slow parser (0.2s per enrich) and batch_size=1 so the first
    enriched position triggers an eager flush.  Asserts that the first
    classify_relevance update_body event in the FakeStatusDisplay call log
    precedes the parser-done update_body event, proving parser/LLM overlap.
    """
    import time as _time

    class _SlowTwoStubParser(_StubParserBase):
        def __enter__(self) -> "_SlowTwoStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=f"https://slow.example/{i}",
                    title=f"Job {i}",
                    source="s",
                )
                for i in range(2)
            ]

        def enrich(self, stub: PositionStub) -> Position:
            _time.sleep(0.2)
            return Position(stub=stub, raw_description="Software engineering role.")

    display = FakeStatusDisplay()

    run(
        _batch_size_config(tmp_path, 1),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _SlowTwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    first_classify_idx = next(
        i
        for i, c in enumerate(display.calls)
        if c.method == "update_body" and c.name == "llm_classify_relevance"
    )
    parser_done_idx = next(
        i
        for i, c in enumerate(display.calls)
        if c.method == "update_body"
        and str(c.kwargs.get("body", "")).endswith(" · done")
    )

    assert first_classify_idx < parser_done_idx, (
        "classify_relevance update_body must precede parser-done update_body"
    )


def test_classify_thread_six_positions_happy_path(tmp_path: Path) -> None:
    """_ClassifyThread happy path: 6 survivors → 6 solo classify calls.

    All positions are in-domain and judged green.  Asserts set-equality on the
    URLs that appear in daily results and on the 'kept' members of .seen.json.
    """
    seen_path = tmp_path / ".seen.json"
    results_dir = tmp_path / "results"

    _URLS_A = [f"https://ct3b.example/a/{i}" for i in range(2)]
    _URLS_B = [f"https://ct3b.example/b/{i}" for i in range(4)]
    _ALL_URLS = set(_URLS_A + _URLS_B)

    class _SixStubParser(_StubParserBase):
        def __enter__(self) -> "_SixStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            stubs = []
            for url in _URLS_A:
                stubs.append(PositionStub(url=url, title=f"Role A {url}", source="s"))
            for url in _URLS_B:
                stubs.append(PositionStub(url=url, title=f"Role B {url}", source="s"))
            return stubs

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="Software engineering role.")

    summary = run(
        _batch_size_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _SixStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    # 6 positions → 6 solo calls; judge_top_n caps at 5
    assert summary.written == 5
    assert summary.classify_items == 6
    assert summary.classifier_dropped == 0

    # .seen.json: 5 URLs kept (top-5 from judge_top_n)
    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    kept_urls = {
        url for url, rec in seen_data.items() if rec["status"] == "selected_by_judge"
    }
    assert len(kept_urls) == 5
    assert kept_urls.issubset(_ALL_URLS)

    # Daily results file: 5 URLs appear in rendered content
    content = _read_all_results(results_dir)
    urls_in_content = {url for url in _ALL_URLS if url in content}
    assert len(urls_in_content) == 5


def test_mixed_listing_set_all_classified(tmp_path: Path) -> None:
    """Mixed-language listings are all classified individually."""
    call_count = [0]

    def _classify(item: ClassifyItem) -> tuple[RelevanceVerdict, CallUsage]:
        call_count[0] += 1
        return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

    ext = MagicMock()
    ext.classify_relevance.side_effect = _classify
    ext.judge_top_n.side_effect = lambda candidates: (
        [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="ok",
                rank=i + 1,
                id=c.id,
            )
            for i, c in enumerate(candidates[:5])
        ],
        _ZERO_USAGE,
    )

    class _FourStubParser(_StubParserBase):
        def __enter__(self) -> "_FourStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://ml.example/1", title="Entwickler", source="s"
                ),
                PositionStub(url="https://ml.example/2", title="Ingenieur", source="s"),
                PositionStub(url="https://ml.example/3", title="Engineer", source="s"),
                PositionStub(url="https://ml.example/4", title="Developer", source="s"),
            ]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="Software engineering role.")

    summary = run(
        _batch_size_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _FourStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.written == 4
    assert call_count[0] == 4


def test_off_domain_marked_seen_immediately_no_judge(tmp_path: Path) -> None:
    """Positions classified as off-domain are not included in judge_top_n candidates."""
    seen_path = tmp_path / ".seen.json"
    _OFF_URL = "https://offdomain.example/0"
    _ON_URL = "https://offdomain.example/1"

    def _classify(item: ClassifyItem) -> tuple[RelevanceVerdict, CallUsage]:
        in_domain = item.raw_description != "off domain content"
        return (
            RelevanceVerdict(
                in_domain=in_domain,
                extract=_STUB_EXTRACT if in_domain else None,
            ),
            _ZERO_USAGE,
        )

    judge_candidate_ids: list[list[str]] = []

    def _judge_top_n(
        candidates: list[JudgeCandidate],
    ) -> tuple[list[MatchVerdict], CallUsage]:
        judge_candidate_ids.append([c.id for c in candidates])
        return [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="ok",
                rank=i + 1,
                id=c.id,
            )
            for i, c in enumerate(candidates[:5])
        ], _ZERO_USAGE

    ext = MagicMock()
    ext.classify_relevance.side_effect = _classify
    ext.judge_top_n.side_effect = _judge_top_n

    class _TwoLangParser(_StubParserBase):
        def __enter__(self) -> "_TwoLangParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=_OFF_URL, title="Off-domain Job", source="s"),
                PositionStub(url=_ON_URL, title="On-domain Job", source="s"),
            ]

        def enrich(self, stub: PositionStub) -> Position:
            if stub.url == _OFF_URL:
                return Position(stub=stub, raw_description="off domain content")
            return Position(stub=stub, raw_description="software engineering role")

    summary = run(
        _batch_size_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoLangParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.classifier_dropped == 1
    assert summary.written == 1
    # judge_top_n called with only the in-domain candidate
    assert len(judge_candidate_ids) == 1
    assert _ON_URL in judge_candidate_ids[0]
    assert _OFF_URL not in judge_candidate_ids[0]

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data[_OFF_URL]["status"] == "out_of_domain"


def test_classify_malformed_position_not_marked_seen(tmp_path: Path) -> None:
    """ExtractorError on classify_relevance: the failing position is not marked seen; run continues."""
    seen_path = tmp_path / ".seen.json"

    call_count = [0]

    def _classify(item: ClassifyItem) -> tuple[RelevanceVerdict, CallUsage]:
        call_count[0] += 1
        if call_count[0] == 1:
            from application_pipeline.llm import ExtractorMalformedError

            raise ExtractorMalformedError("bad verdict")
        return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

    ext = MagicMock()
    ext.classify_relevance.side_effect = _classify
    ext.judge_top_n.side_effect = lambda candidates: (
        [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="ok",
                rank=i + 1,
                id=c.id,
            )
            for i, c in enumerate(candidates[:5])
        ],
        _ZERO_USAGE,
    )

    summary = run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    # First call failed (1 item), second succeeded (1 item written)
    assert summary.errored == 1
    assert summary.written == 1

    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    # First item must NOT be in seen store
    assert _ERR_URLS[0] not in seen_data


def test_classify_failure_logs_to_classify_relevance_events(
    tmp_path: Path,
) -> None:
    """ExtractorError on classify_relevance is logged to classify_relevance.events.jsonl."""
    import application_pipeline.parser_log as pl
    from application_pipeline.llm import ExtractorMalformedError

    logs_dir = tmp_path / "synched" / "logs"
    run_log = pl.RunLog(logs_dir)

    ext = MagicMock()
    ext.classify_relevance.side_effect = ExtractorMalformedError("bad verdict")

    run(
        _batch_size_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_file = logs_dir / "llm_classify_relevance.events.jsonl"
    assert events_file.exists(), (
        "classify_relevance.events.jsonl must be created on classify error"
    )


def test_classify_failure_writes_one_event_per_position(tmp_path: Path) -> None:
    """ExtractorError on classify_relevance: one event row per failing position."""
    import application_pipeline.parser_log as pl

    logs_dir = tmp_path / "synched" / "logs"
    run_log = pl.RunLog(logs_dir)

    ext = MagicMock()
    ext.classify_relevance.side_effect = ExtractorError("classify boom")

    run(
        _batch_size_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_file = logs_dir / "llm_classify_relevance.events.jsonl"
    events_rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    # 2 positions → 2 classify_relevance failure events (one per position)
    assert len(events_rows) == 2


def test_classify_failure_event_written_on_extractor_error(tmp_path: Path) -> None:
    """ExtractorError on classify_relevance: events file exists (logged by classify thread)."""
    import application_pipeline.parser_log as pl

    logs_dir = tmp_path / "synched" / "logs"
    run_log = pl.RunLog(logs_dir)

    ext = MagicMock()
    ext.classify_relevance.side_effect = ExtractorError("classify gone")

    run(
        _batch_size_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_file = logs_dir / "llm_classify_relevance.events.jsonl"
    assert events_file.exists(), "llm_classify_relevance.events.jsonl must be written"


def test_judge_error_log_includes_forensic_fields(tmp_path: Path) -> None:
    """ExtractorUnreachableError with forensics → returncode and stderr_excerpt in judge_top_n log."""
    import application_pipeline.parser_log as pl

    logs_dir = tmp_path / "synched" / "logs"
    run_log = pl.RunLog(logs_dir)

    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = ExtractorUnreachableError(
        "cli gone", returncode=2, stderr="timeout on judge"
    )

    run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_rows = [
        json.loads(line)
        for line in (logs_dir / "llm_judge_top_n.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(row.get("returncode") == 2 for row in events_rows)
    assert any(row.get("stderr_excerpt") == "timeout on judge" for row in events_rows)


# ---------------------------------------------------------------------------
# Prompt loader: only de + en; init materialises only de + en
# ---------------------------------------------------------------------------


def test_prompt_loader_returns_single_template_per_call_site(tmp_path: Path) -> None:
    """load_prompts returns a single PromptTemplate per call site."""
    from application_pipeline.prompts import load_prompts
    from application_pipeline import Config, PromptTemplate, SourceEntry

    user_info_dir = tmp_path / "user-info"
    user_info_dir.mkdir()
    triage_dir = user_info_dir / "triage-profile"
    triage_dir.mkdir()
    (triage_dir / "self-description.md").write_text("dev background\n")
    (triage_dir / "domain-fit.md").write_text("ML roles\n")
    (triage_dir / "match-criteria.md").write_text("Hamburg, remote\n")

    cfg = Config(
        sources=[SourceEntry(parser_type="bundesagentur_api")],
        locations=["Hamburg"],
        user_info_dir=user_info_dir,
    )
    prompts = load_prompts(cfg)

    assert isinstance(prompts.classify_relevance, PromptTemplate)
    assert isinstance(prompts.judge_match, PromptTemplate)


def test_init_materialises_user_info_files(
    tmp_path: Path,
) -> None:
    """init command seeds the three triage-profile template files."""
    from application_pipeline.init_cmd import init

    init(tmp_path)

    triage_files = {
        f.name for f in (tmp_path / "user-info" / "triage-profile").glob("*.md")
    }

    assert "self-description.md" in triage_files
    assert "domain-fit.md" in triage_files
    assert "match-criteria.md" in triage_files


# ---------------------------------------------------------------------------
# RunSummary telemetry (issue #188)
# ---------------------------------------------------------------------------


def test_run_summary_carries_token_and_cost_totals(tmp_path: Path) -> None:
    """RunSummary accumulates classify + judge token/cost totals from _FakeExtractor."""
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
        extractor=_FakeExtractor(),
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    # 5 items pass prefilter → 5 solo classify calls
    assert summary.classify_items == 5
    # 5 classify calls × 10 input tokens + 1 judge_top_n call × 8 input tokens
    assert summary.claude_input_tokens == 5 * 10 + 8
    assert summary.claude_output_tokens == 5 * 5 + 4
    assert summary.claude_cache_read_tokens == 5 * 2 + 1
    assert abs(summary.claude_cost_usd - (5 * 0.001 + 0.0008)) < 1e-9


def test_classify_relevance_trailer_schema(tmp_path: Path) -> None:
    """classify_relevance.log gets a SUMMARY OF SESSION with the full expected schema."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

    seen_path = tmp_path / ".seen.json"
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
        run_log=run_log,
    )

    run_log_file = logs_dir / "run.log"
    assert run_log_file.exists(), "run.log must be created"
    content = run_log_file.read_text(encoding="utf-8")

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
        assert key in content, f"key {key!r} missing from run.log"


def test_judge_match_trailer_schema(tmp_path: Path) -> None:
    """judge_match.log gets a SUMMARY OF SESSION with the full expected schema."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

    seen_path = tmp_path / ".seen.json"
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
        run_log=run_log,
    )

    run_log_file = logs_dir / "run.log"
    assert run_log_file.exists(), "run.log must be created"
    content = run_log_file.read_text(encoding="utf-8")

    assert "SUMMARY OF SESSION" in content
    for key in (
        "judges_sent=",
        "judges_failed=",
        "input_tokens=",
        "output_tokens=",
        "cache_read_tokens=",
        "cost_usd=",
        "duration_s=",
    ):
        assert key in content, f"key {key!r} missing from run.log"


def test_main_run_complete_line_includes_new_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """__main__ 'run complete:' line includes classify_items, claude_* token and cost fields."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "application-pipeline").mkdir()
    (tmp_path / "application-pipeline" / "config.py").write_text("")
    monkeypatch.setattr("sys.argv", ["app", "run"])

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
        "application_pipeline.orchestrator.run", lambda *_a, **_kw: fake_summary
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
        status_display=display,
    )

    assert display.stopped


def test_display_parser_log_records_pipeline_register(tmp_path: Path) -> None:
    """pipeline register() writes a lifecycle record to lifecycle.jsonl via parser_log."""
    from application_pipeline.parser_log import RunLog

    run_log = RunLog(tmp_path / "logs")
    config_path = _write_config(tmp_path)

    from application_pipeline.status_display import PlainStatusDisplay

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
        status_display=PlainStatusDisplay(run_log=run_log),
        run_log=run_log,
    )

    lifecycle_rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "lifecycle.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        row.get("event") == "registered"
        and row.get("component") == "pipeline"
        and row.get("order") == 0
        for row in lifecycle_rows
    )


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
        status_display=display,
    )

    assert "parser_bundesagentur_api" in display.registered_names()
    reg = next(
        c
        for c in display.calls
        if c.method == "register" and c.name == "parser_bundesagentur_api"
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
        status_display=display,
    )

    indexed = [(i, c.method, c.name) for i, c in enumerate(display.calls)]
    startup_reg_idx = next(
        i for i, m, n in indexed if m == "register" and n == "startup"
    )
    parser_reg_idx = next(
        i for i, m, n in indexed if m == "register" and n == "parser_bundesagentur_api"
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
        status_display=display,
    )

    bodies = display.body_updates_for("parser_bundesagentur_api")
    assert bodies, "expected at least one body update for parser row"
    assert bodies[-1].endswith("· done"), (
        f"last body {bodies[-1]!r} must end with '· done'"
    )
    assert not any(
        c.method == "remove" and c.name == "parser_bundesagentur_api"
        for c in display.calls
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
        status_display=display,
    )

    bodies = display.body_updates_for("parser_bundesagentur_api")
    # Last body before "done" should have format: "X/Y queries · N stubs · M enriched · done"
    final = bodies[-1]
    assert "queries" in final
    assert "stubs" in final
    assert "enriched" in final
    # _StubParser returns 3 stubs per call; 1 keyword × 1 location = 1 query → 3 stubs, 3 enriched
    assert final.startswith("1/1 queries · 3 stubs · 3 enriched")


def test_parser_row_body_shows_dead_on_crash(tmp_path: Path) -> None:
    """Parser row body gains '· dead' suffix when parser thread crashes."""

    class _DeadParserForRow(_StubParserBase):
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
        status_display=display,
    )

    bodies = display.body_updates_for("parser_bundesagentur_api")
    assert bodies, "expected at least one body update for dead parser row"
    assert bodies[-1].endswith("· dead"), (
        f"last body {bodies[-1]!r} must end with '· dead'"
    )
    assert not any(
        c.method == "remove" and c.name == "parser_bundesagentur_api"
        for c in display.calls
    ), "dead parser row must not be removed"


def test_multiple_parser_rows_each_registered(tmp_path: Path) -> None:
    """Multiple parsers each get their own row with distinct order values."""

    class _EmptyParser(_StubParserBase):
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
        status_display=display,
    )

    registered = display.registered_names()
    assert "parser_bundesagentur_api" in registered
    assert "parser_stellen_hamburg_api" in registered

    order_a = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "parser_bundesagentur_api"
    )
    order_b = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "parser_stellen_hamburg_api"
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
        status_display=display,
    )

    registered = display.registered_names()
    assert "pipeline_dedup" in registered
    assert "pipeline_prefilter" in registered

    dedup_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "pipeline_dedup"
    )
    prefilter_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "pipeline_prefilter"
    )
    parser_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "parser_bundesagentur_api"
    )
    assert dedup_order > parser_order
    assert prefilter_order == dedup_order + 1


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
        status_display=display,
    )

    bodies = display.body_updates_for("pipeline_prefilter")
    assert bodies, "expected at least one body update for pipeline_prefilter row"
    final = bodies[-1]
    assert "considered=" in final
    assert "passed=" in final
    assert "dropped=" in final
    assert "bl=" in final
    assert "wl=" not in final


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
        status_display=display,
    )

    assert not any(
        c.method == "remove" and c.name == "pipeline_dedup" for c in display.calls
    ), "pipeline_dedup row must not be removed during run"
    assert not any(
        c.method == "remove" and c.name == "pipeline_prefilter" for c in display.calls
    ), "pipeline_prefilter row must not be removed during run"


# ---------------------------------------------------------------------------
# Status Display: classify_relevance and judge_match rows (issue #199)
# ---------------------------------------------------------------------------

_DE_DESCRIPTION_199 = (
    "Wir suchen einen erfahrenen Softwareentwickler für unser Team. "
    "Das Unternehmen bietet interessante Projekte und eine gute Bezahlung."
)


class _MixedLangParser199(_StubParserBase):
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
        status_display=display,
    )

    assert "llm_classify_relevance" in display.registered_names()
    assert "llm_judge_match" in display.registered_names()

    prefilter_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "pipeline_prefilter"
    )
    classify_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "llm_classify_relevance"
    )
    judge_order = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "llm_judge_match"
    )

    assert classify_order > prefilter_order
    assert judge_order == classify_order + 1


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
        status_display=display,
    )

    assert not any(
        c.method == "remove" and c.name == "llm_classify_relevance"
        for c in display.calls
    ), "classify_relevance row must not be removed during run"
    assert not any(
        c.method == "remove" and c.name == "llm_judge_match" for c in display.calls
    ), "judge_match row must not be removed during run"


# ---------------------------------------------------------------------------
# Stuck-thread watchdog
# ---------------------------------------------------------------------------


def test_stall_watchdog_logs_stalled_and_stack_trace(tmp_path: Path) -> None:
    """Parser that sleeps past the stall threshold emits 'stalled' + stack trace in its log."""
    import time

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

    _THRESHOLD = 0.05  # 50 ms — fast enough for tests

    class _SleepyParser(_StubParserBase):
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
        stall_threshold_s=_THRESHOLD,
        run_log=run_log,
    )

    events_file = logs_dir / "parser_bundesagentur_api.events.jsonl"
    assert events_file.exists(), "events log file must be created"
    events_content = events_file.read_text(encoding="utf-8")
    assert "stalled" in events_content, "stalled event must appear in events log"

    run_log_content = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert "traceback" in run_log_content, "stack trace header must appear in run.log"
    assert "File " in run_log_content, "stack frame lines must appear in run.log"


def test_stall_watchdog_fires_only_once_per_silence(tmp_path: Path) -> None:
    """Stall is logged at most once per silence period — not on every poll tick."""
    import time

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

    _THRESHOLD = 0.05

    class _LongSleepParser(_StubParserBase):
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
        stall_threshold_s=_THRESHOLD,
        run_log=run_log,
    )

    events_file = logs_dir / "parser_bundesagentur_api.events.jsonl"
    events_rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    stalled_count = sum(1 for row in events_rows if row.get("event") == "stalled")
    assert stalled_count == 1, f"expected exactly 1 stalled entry, got {stalled_count}"


# ---------------------------------------------------------------------------
# _ParserThread: query_started / query_ended heartbeats (issue #208)
# ---------------------------------------------------------------------------


def test_query_heartbeats_n_started_and_n_ended(tmp_path: Path) -> None:
    """N queries → exactly N query_started and N query_ended lines in the parser log."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

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
        run_log=run_log,
    )

    events_file = logs_dir / "parser_bundesagentur_api.events.jsonl"
    events_rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    # 2 keywords × 1 location = 2 queries
    started_count = sum(1 for row in events_rows if row.get("event") == "query_started")
    ended_count = sum(1 for row in events_rows if row.get("event") == "query_ended")
    assert started_count == 2, f"expected 2 query_started lines, got {started_count}"
    assert ended_count == 2, f"expected 2 query_ended lines, got {ended_count}"


def test_query_ended_fires_even_when_discover_raises(tmp_path: Path) -> None:
    """query_ended is written even when discover() raises mid-query (parser dies)."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

    class _RaisingParser(_StubParserBase):
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
        run_log=run_log,
    )

    assert summary.parsers_dead == 1

    events_file = logs_dir / "parser_bundesagentur_api.events.jsonl"
    events_rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    assert any(row.get("event") == "query_started" for row in events_rows), (
        "query_started must be logged before the crash"
    )
    assert any(row.get("event") == "query_ended" for row in events_rows), (
        "query_ended must fire even when discover() raises"
    )


# ---------------------------------------------------------------------------
# Non-quota abort (issue #217)
# ---------------------------------------------------------------------------


def test_non_quota_worker_exception_writes_failure_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RuntimeError from judge worker → failure report written, exit 1, no Run Divider."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "application-pipeline").mkdir()
    _write_config(tmp_path / "application-pipeline")
    monkeypatch.setattr("sys.argv", ["app", "run"])

    class _AbortingExtractor:
        def classify_relevance(
            self, item: ClassifyItem
        ) -> tuple[RelevanceVerdict, CallUsage]:
            return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            raise RuntimeError("disk full")

    monkeypatch.setattr(
        "application_pipeline.orchestrator.ClaudeExtractor",
        lambda *a, **kw: _AbortingExtractor(),
    )

    class _OneStubParser(_StubParserBase):
        def __enter__(self) -> "_OneStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://abort.example/0",
                    title="Job",
                    source="s",
                )
            ]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="software engineering role")

    monkeypatch.setattr(
        "application_pipeline.orchestrator._default_registry",
        type("_Reg", (), {"get": staticmethod(lambda _: _OneStubParser)})(),
    )

    from application_pipeline.__main__ import main

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1

    failures_dir = tmp_path / "application-pipeline" / "failures"
    reports = list(failures_dir.glob("*.md")) if failures_dir.exists() else []
    assert len(reports) == 1, f"expected one failure report, got {reports}"

    body = reports[0].read_text(encoding="utf-8")
    assert "RuntimeError" in body


# ---------------------------------------------------------------------------
# Issue #229 — status-row body refreshed on error exit paths
# ---------------------------------------------------------------------------


def test_classify_error_refreshes_status_body(tmp_path: Path) -> None:
    """ExtractorError on classify: classify_relevance row body is refreshed with calls_failed=N items_failed=M."""
    ext = MagicMock()
    ext.classify_relevance.side_effect = ExtractorError("classify boom")

    display = FakeStatusDisplay()

    run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    classify_bodies = display.body_updates_for("llm_classify_relevance")
    assert classify_bodies, "expected at least one classify_relevance body update"
    last_body = classify_bodies[-1]
    assert "calls_failed=2" in last_body
    assert "items_failed=2" in last_body


def test_judge_error_written_is_zero(tmp_path: Path) -> None:
    """ExtractorError on judge_top_n: no positions written, run completes without raising."""
    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = ExtractorError("judge boom")

    display = FakeStatusDisplay()

    summary = run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    assert summary.written == 0, "no positions should be written when judge_top_n fails"
    assert "llm_judge_match" in display.registered_names()


def test_clean_run_bodies_contain_no_error_tokens(tmp_path: Path) -> None:
    """On a clean run, classify and judge bodies contain no error tokens."""
    display = FakeStatusDisplay()

    run(
        _two_stub_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    for body in display.body_updates_for("llm_classify_relevance"):
        assert "calls_failed=" not in body
        assert "items_failed=" not in body

    for body in display.body_updates_for("llm_judge_match"):
        assert "calls_failed=" not in body


def test_judge_body_shows_finished_calls(tmp_path: Path) -> None:
    """judge_top_n success: llm_judge_match body shows 1/1 calls with no error tokens."""
    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = lambda candidates: (
        [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="ok",
                rank=i + 1,
                id=c.id,
            )
            for i, c in enumerate(candidates[:5])
        ],
        _ZERO_USAGE,
    )

    display = FakeStatusDisplay()

    run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    judge_bodies = display.body_updates_for("llm_judge_match")
    assert judge_bodies, "expected judge_match body updates"
    last_body = judge_bodies[-1]
    # 1 successful judge_top_n call
    assert "1/1 calls" in last_body
    assert "calls_failed=" not in last_body


# ---------------------------------------------------------------------------
# Issue #230 — live pending-depth signal
# ---------------------------------------------------------------------------


def test_pending_drains_to_zero_on_clean_run(tmp_path: Path) -> None:
    """Pending figures return to zero at end-of-run on a clean run."""
    display = FakeStatusDisplay()

    run(
        _batch_size_config(tmp_path, 1),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _MixedLangParser199,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    classify_bodies = display.body_updates_for("llm_classify_relevance")
    assert "0 items in queue" in classify_bodies[-1], (
        f"Classify pending should be 0 at end-of-run: {classify_bodies[-1]!r}"
    )

    judge_bodies = display.body_updates_for("llm_judge_match")
    assert "0 items in queue" in judge_bodies[-1], (
        f"Judge pending should be 0 at end-of-run: {judge_bodies[-1]!r}"
    )


# ---------------------------------------------------------------------------
# Issue #233: classify batch failures logged to classify_relevance events
# ---------------------------------------------------------------------------


def test_classify_failure_logs_events_per_position(
    tmp_path: Path,
) -> None:
    """A failing classify_relevance call logs one event per position to classify_relevance.events.jsonl."""
    import application_pipeline.parser_log as pl

    logs_dir = tmp_path / "logs"
    run_log = pl.RunLog(logs_dir)

    ext = MagicMock()
    ext.classify_relevance.side_effect = ExtractorError("classify boom")

    run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_rows = [
        json.loads(line)
        for line in (logs_dir / "llm_classify_relevance.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    # 2 positions → 2 failure events
    assert len(events_rows) == 2


def test_judge_top_n_failure_leaves_no_daily_file(
    tmp_path: Path,
) -> None:
    """A run where judge_top_n fails does not write any cards to the daily file."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()

    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = ExtractorError("judge boom")

    run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    results_dir = tmp_path / "results"
    dated_file = results_dir / f"{today}.md"
    if dated_file.exists():
        content = dated_file.read_text(encoding="utf-8")
        cards = re.findall(r"^# .+ · .+", content, re.MULTILINE)
        assert cards == [], (
            f"no cards should be written on judge_top_n failure: {cards}"
        )


def test_clean_run_writes_classify_success_events(tmp_path: Path) -> None:
    """A clean run logs one classify_relevance success event per position."""
    import application_pipeline.parser_log as pl

    logs_dir = tmp_path / "logs"
    run_log = pl.RunLog(logs_dir)

    run(
        _two_stub_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_file = logs_dir / "llm_classify_relevance.events.jsonl"
    assert events_file.exists()
    rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    success_rows = [r for r in rows if r.get("event") == "classify_relevance"]
    assert len(success_rows) == 2


def test_classify_failure_does_not_set_degraded_reason(tmp_path: Path) -> None:
    """A classify_relevance failure does not cause the run to set degraded_reason."""
    ext = MagicMock()
    ext.classify_relevance.side_effect = ExtractorError("classify boom")

    summary = run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    # An abandoned classify batch should not abort the run
    assert isinstance(summary, RunSummary)


# ---------------------------------------------------------------------------
# Config-derived paths (issue #300)
# ---------------------------------------------------------------------------


def test_results_file_lands_under_config_dir_regardless_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daily results dir is created under <config_dir>/results/, not relative to CWD."""
    config_path = _write_config(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
    )

    # ensure_initialized creates the results dir under config_dir, not CWD
    assert (tmp_path / "results").is_dir()
    assert not (other_dir / "results").exists()


# ---------------------------------------------------------------------------
# Per-tier results files (issue #314)
# ---------------------------------------------------------------------------


def test_verdicts_written_to_daily_results_file(tmp_path: Path) -> None:
    """All judge_top_n verdicts are written to the daily results file."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    seen_path = tmp_path / ".seen.json"

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
            negative_keywords='["excluded"]',
        ),
        extractor=_FakeExtractor(),
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    results_dir = tmp_path / "results"
    dated_file = results_dir / f"{today}.md"
    assert dated_file.exists(), f"Expected daily file at {dated_file}"

    # _FakeExtractor: 4 in-domain stubs (after 1 prefilter-drop and 1 classify-drop)
    # judge_top_n returns up to 5 verdicts
    content = dated_file.read_text(encoding="utf-8")
    cards = re.findall(r"^# .+ · .+", content, re.MULTILINE)
    assert len(cards) == 4
    assert summary.written == 4


def test_daily_file_written_event_logged(tmp_path: Path) -> None:
    """daily_file_written event is logged to pipeline_orchestrator.events.jsonl when cards are written."""
    import application_pipeline.parser_log as parser_log

    seen_path = tmp_path / ".seen.json"
    logs_dir = tmp_path / "logs"
    run_log = parser_log.RunLog(logs_dir)
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
        run_log=run_log,
    )

    events_file = logs_dir / "pipeline_orchestrator.events.jsonl"
    assert events_file.exists()
    rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    daily_written = [r for r in rows if r.get("event") == "daily_file_written"]
    assert len(daily_written) == 1
    assert daily_written[0]["card_count"] == 4


def test_second_run_skips_all_urls_and_seen_json_unchanged(tmp_path: Path) -> None:
    """Second run: all URLs skipped, .seen.json unchanged."""
    seen_path = tmp_path / ".seen.json"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )

    first = run(
        config_path,
        extractor=_FakeExtractor(),
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    seen_after_first = json.loads(seen_path.read_text(encoding="utf-8"))

    second = run(
        config_path,
        extractor=_FakeExtractor(),
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    seen_after_second = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_after_first == seen_after_second, (
        "Second run must not change .seen.json"
    )
    assert second.skipped == first.discovered, (
        "All URLs from first run must be skipped on second run"
    )
    assert second.written == 0


def test_old_md_files_ignored_in_results_dir(tmp_path: Path) -> None:
    """Non-dated files in results dir (e.g. current.md) are not touched by the run."""
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True)
    current_md = results_dir / "current.md"
    current_md.write_text("old content", encoding="utf-8")

    run(
        _write_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
    )

    # current.md must be untouched
    assert current_md.read_text(encoding="utf-8") == "old content"


# ---------------------------------------------------------------------------
# Quota sleep-and-retry (issue #388)
# ---------------------------------------------------------------------------


def test_quota_classify_retries_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ClaudeUsageLimitError on classify → orchestrator sleeps and retries; run completes."""
    seen_path = tmp_path / ".seen.json"

    slept: list[float] = []
    monkeypatch.setattr("application_pipeline.orchestrator.time.sleep", slept.append)

    call_count = [0]

    def _classify(item: ClassifyItem) -> tuple[RelevanceVerdict, CallUsage]:
        call_count[0] += 1
        if call_count[0] == 1:
            raise ClaudeUsageLimitError(
                "subscription cap",
                returncode=1,
                stdout="",
                stderr="subscription cap",
                envelope=None,
            )
        return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

    ext = MagicMock()
    ext.classify_relevance.side_effect = _classify
    ext.judge_top_n.side_effect = lambda candidates: (
        [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="ok",
                rank=i + 1,
                id=c.id,
            )
            for i, c in enumerate(candidates[:5])
        ],
        _ZERO_USAGE,
    )

    summary = run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.written == 2
    assert len(slept) == 1
    assert slept[0] > 0


def test_quota_sleep_event_logged_to_pipeline_orchestrator_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """quota_sleep event is written to pipeline_orchestrator.events.jsonl with required fields."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "logs"
    run_log = parser_log.RunLog(logs_dir)
    monkeypatch.setattr("application_pipeline.orchestrator.time.sleep", lambda _: None)

    call_count = [0]

    def _classify(item: ClassifyItem) -> tuple[RelevanceVerdict, CallUsage]:
        call_count[0] += 1
        if call_count[0] == 1:
            raise ClaudeUsageLimitError(
                "cap",
                returncode=1,
                stdout="",
                stderr="cap",
                envelope=None,
            )
        return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

    ext = MagicMock()
    ext.classify_relevance.side_effect = _classify
    ext.judge_top_n.side_effect = lambda candidates: (
        [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="ok",
                rank=i + 1,
                id=c.id,
            )
            for i, c in enumerate(candidates[:5])
        ],
        _ZERO_USAGE,
    )

    run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_file = logs_dir / "pipeline_orchestrator.events.jsonl"
    assert events_file.exists(), "pipeline_orchestrator.events.jsonl must be written"
    rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    quota_rows = [r for r in rows if r.get("event") == "quota_sleep"]
    assert len(quota_rows) == 1
    row = quota_rows[0]
    assert "wake_time" in row
    assert "duration_s" in row
    assert "reset_time" in row


def test_quota_judge_retries_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ClaudeUsageLimitError on judge_top_n → orchestrator sleeps and retries; run completes."""
    seen_path = tmp_path / ".seen.json"

    slept: list[float] = []
    monkeypatch.setattr("application_pipeline.orchestrator.time.sleep", slept.append)

    judge_call_count = [0]

    def _judge_top_n(
        candidates: list[JudgeCandidate],
    ) -> tuple[list[MatchVerdict], CallUsage]:
        judge_call_count[0] += 1
        if judge_call_count[0] == 1:
            raise ClaudeUsageLimitError(
                "quota", returncode=1, stdout="", stderr="quota", envelope=None
            )
        return [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="ok",
                rank=i + 1,
                id=c.id,
            )
            for i, c in enumerate(candidates[:5])
        ], _ZERO_USAGE

    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = _judge_top_n

    summary = run(
        _two_stub_config(tmp_path),
        extractor=ext,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.written == 2
    assert len(slept) >= 1


# ---------------------------------------------------------------------------
# Daily cutover — issue #390
# ---------------------------------------------------------------------------

_DAILY390_URL = "https://daily390.example/job-1"


class _Daily390Parser(_StubParserBase):
    """Single-stub parser used by the issue-390 daily cutover tests."""

    def __enter__(self) -> "_Daily390Parser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return [PositionStub(url=_DAILY390_URL, title="Python Dev", source="stub")]

    def enrich(self, stub: PositionStub) -> Position:
        return Position(stub=stub, raw_description="good job description")


def test_successful_run_writes_dated_daily_file(tmp_path: Path) -> None:
    """Successful run writes data/results/YYYY-MM-DD.md; trio files are not written."""
    from datetime import datetime, timezone

    seen_path = tmp_path / ".seen.json"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    today = datetime.now(timezone.utc).date().isoformat()

    ext = MagicMock()
    ext.classify_relevance.side_effect = lambda item: (
        RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT),
        _ZERO_USAGE,
    )
    ext.judge_top_n.return_value = (
        [
            MatchVerdict(
                matched=[],
                missing=[],
                summary="great match",
                rank=1,
                id=_DAILY390_URL,
            )
        ],
        _ZERO_USAGE,
    )

    run(
        config_path,
        extractor=ext,
        parser_registry=lambda _: _Daily390Parser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
    )

    results_dir = tmp_path / "results"
    dated_file = results_dir / f"{today}.md"
    assert dated_file.exists(), f"Expected daily file at {dated_file}"
    assert not (results_dir / "green.md").exists(), "green.md must not be written"
    assert not (results_dir / "amber.md").exists(), "amber.md must not be written"
    assert not (results_dir / "red.md").exists(), "red.md must not be written"


# ---------------------------------------------------------------------------
# Freshness Gate integration (issue #398)
# ---------------------------------------------------------------------------

from datetime import date as _date, timedelta as _timedelta  # noqa: E402


def test_freshness_pool_reentry_expired_deletes_extract(tmp_path: Path) -> None:
    """in_domain → expired transition on pool re-discovery removes the entry from extracts.json."""
    stale_url = "https://pool-reentry.example/stale-extract"
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"

    seen_path.write_text(
        json.dumps(
            {
                stale_url: {
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "in_domain",
                    "first_seen": "2024-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    # Write a pre-existing extract for the URL
    extracts_path.write_text(
        json.dumps(
            {
                stale_url: {
                    "seniority": None,
                    "work_model": None,
                    "contract_type": None,
                    "key_skills": [],
                    "key_responsibilities": [],
                    "must_have_requirements": [],
                    "notable_caveats": "",
                }
            }
        ),
        encoding="utf-8",
    )

    today = _date.today()

    class _StaleExtractParser(_StubParserBase):
        def __enter__(self) -> "_StaleExtractParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=stale_url, title="Extract Job", source="stub")]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(
                stub=stub,
                raw_description="stale",
                posted_date=today - _timedelta(days=500),
            )

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StaleExtractParser,  # type: ignore[return-value]
        # no dedup_store= so orchestrator wires extract_store into dedup_store
    )

    seen_after = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_after[stale_url]["status"] == "expired"
    extracts_after = json.loads(extracts_path.read_text(encoding="utf-8"))
    assert stale_url not in extracts_after


def test_freshness_pool_reentry_fresh_position_stays_in_domain_and_reaches_judge(
    tmp_path: Path,
) -> None:
    """A still-fresh in_domain URL passes the gate, stays in_domain, and is judged."""
    fresh_url = "https://pool-reentry.example/fresh"
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"

    seen_path.write_text(
        json.dumps(
            {
                fresh_url: {
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "in_domain",
                    "first_seen": "2024-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    extracts_path.write_text(
        json.dumps(
            {
                fresh_url: {
                    "seniority": None,
                    "work_model": None,
                    "contract_type": None,
                    "key_skills": [],
                    "key_responsibilities": [],
                    "must_have_requirements": [],
                    "notable_caveats": "",
                }
            }
        ),
        encoding="utf-8",
    )

    today = _date.today()
    judge_candidates: list[JudgeCandidate] = []

    class _FreshPoolParser(_StubParserBase):
        def __enter__(self) -> "_FreshPoolParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=fresh_url, title="Fresh Pool Job", source="stub")]

        def enrich(self, stub: PositionStub) -> Position:
            return Position(
                stub=stub,
                raw_description="fresh pool job",
                posted_date=today,
            )

    class _JudgeTrackingExtractor:
        def classify_relevance(
            self, item: ClassifyItem
        ) -> tuple[RelevanceVerdict, CallUsage]:
            return RelevanceVerdict(in_domain=True, extract=_STUB_EXTRACT), _ZERO_USAGE

        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            judge_candidates.extend(candidates)
            return [
                MatchVerdict(matched=[], missing=[], summary="ok", rank=i + 1, id=c.id)
                for i, c in enumerate(candidates[:5])
            ], _ZERO_USAGE

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_JudgeTrackingExtractor(),
        parser_registry=lambda _: _FreshPoolParser,  # type: ignore[return-value]
    )

    seen_data_after = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data_after[fresh_url]["status"] != "expired"
    assert any(c.id == fresh_url for c in judge_candidates)
