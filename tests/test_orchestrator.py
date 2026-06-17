from __future__ import annotations

import ast
import json
import re
import textwrap
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fake_status_display import FakeStatusDisplay

from application_pipeline import dedup as dedup_module
from application_pipeline.config import ConfigError
from application_pipeline.dedup import DedupStoreError, DeduplicationStore
from application_pipeline.llm import (
    CallUsage,
    ExtractorError,
    ExtractorUnreachableError,
)
from application_pipeline.extracts.card_store import (
    CardExtract,
    CardStore,
    load_card_store,
)
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.llm.types import (
    AppliedClassifyItemOutcome,
    AppliedClassifyOutcome,
    ClassifyItem,
    ExtractorBatchMalformedError,
    JudgeCandidate,
    MatchVerdict,
    MatchedListing,
    RelevanceVerdict,
)
from application_pipeline.llm.claude_cli import ClaudeUsageLimitError
from application_pipeline.orchestrator import RunSummary, run
from application_pipeline.parsers import (
    Parser,
    ParserQuery,
    PositionStub,
)
from application_pipeline.parsers.body_fetch import OversizedBodyError
from application_pipeline.parsers.errors import ParserError
from application_pipeline.parsers.types import (
    City,
    EnrichFailedError,
    EnrichResult,
    Remote,
)
from application_pipeline.prompts import PromptError
from application_pipeline.daily_results_file import DailyResultsFile, ResultsFileError
from application_pipeline.parser_log import RunLog


class _StubParserBase:
    """Base for all test stub parsers; absorbs the run_log kwarg the orchestrator injects."""

    def __init__(self, **_: object) -> None:
        pass

    def enrich(self, stub: PositionStub) -> EnrichResult:
        return EnrichResult(stub=stub, body="stub body " + "x" * 91, mode="fallback")


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
    claude_classify_parallelism: int | None = None,
    claude_classify_batch_size: int = 1,
) -> Path:
    """Write a minimal valid config.py and a user-info dir into tmp_path."""
    config_path = tmp_path / "config.py"
    parallelism_line = (
        f"CLAUDE_CLASSIFY_PARALLELISM = {claude_classify_parallelism}\n"
        if claude_classify_parallelism is not None
        else ""
    )
    config_path.write_text(
        textwrap.dedent(f"""
            from application_pipeline import SourceEntry
            KEYWORDS = {keywords}
            SKILLS = ["django"]
            SOURCES = {sources}
            LOCATIONS = {locations}
            INCLUDE_REMOTE = {include_remote!r}
            NEGATIVE_KEYWORDS = {negative_keywords}
            CLAUDE_CLASSIFY_BATCH_SIZE = {claude_classify_batch_size}
        """)
        + parallelism_line,
        encoding="utf-8",
    )
    user_info_dir = tmp_path / "user-info"
    user_info_dir.mkdir(exist_ok=True)
    if with_user_info_files:
        triage_dir = user_info_dir / "triage-profile"
        triage_dir.mkdir(exist_ok=True)
        (triage_dir / "candidate-profile.md").write_text("dev background\n")
        (triage_dir / "gate-criteria.md").write_text("Hamburg, remote\n")
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


def _stub_extractor() -> MagicMock:
    ext = MagicMock()
    ext.classify_relevance.return_value = (
        [RelevanceVerdict(matches=False)],
        _ZERO_USAGE,
    )
    ext.judge_top_n.side_effect = lambda candidates: (
        [MatchVerdict(id=c.id, rank=i + 1) for i, c in enumerate(candidates[:5])],
        _ZERO_USAGE,
    )
    return ext


def _make_card_store(tmp_path: Path, name: str = "card_store.json") -> CardStore:
    """Create an empty CardStore backed by tmp_path."""
    return load_card_store(tmp_path / name)


def _dedup_run_complete_row(logs_dir: Path) -> dict[str, object]:
    rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "dedup.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    run_complete_rows = [row for row in rows if row.get("event") == "run_complete"]
    assert len(run_complete_rows) == 1
    return run_complete_rows[0]


def _make_fake_llm_enricher(
    card_store: CardStore,
    *,
    matches: bool = True,
    header: str = "Test Role · ACME · Hamburg",
    summary: str = "A test role description.",
    dedup_store: DeduplicationStore | None = None,
) -> "_FakeLLMEnricherHelper":
    """Create a fake LLMEnricher that applies classify outcomes."""
    return _FakeLLMEnricherHelper(
        card_store,
        matches=matches,
        header=header,
        summary=summary,
        dedup_store=dedup_store,
    )


def _matched_outcome(
    items: list[tuple[int, PositionStub, str]],
) -> AppliedClassifyOutcome:
    return AppliedClassifyOutcome(
        items=[
            AppliedClassifyItemOutcome(
                state="matched",
                event_matches=True,
                matched_listing=MatchedListing(listing_id=listing_id, stub=stub),
            )
            for listing_id, stub, _ in items
        ]
    )


def _rejected_outcome(
    items: list[tuple[int, PositionStub, str]],
) -> AppliedClassifyOutcome:
    return AppliedClassifyOutcome(
        items=[
            AppliedClassifyItemOutcome(state="rejected", event_matches=False)
            for _ in items
        ]
    )


def _retryable_outcome(
    items: list[tuple[int, PositionStub, str]],
) -> AppliedClassifyOutcome:
    return AppliedClassifyOutcome(
        items=[
            AppliedClassifyItemOutcome(state="retryable", event_matches=None)
            for _ in items
        ]
    )


class _FakeLLMEnricherHelper:
    """Fake LLMEnricher for tests that need real enrich + classify behaviour."""

    def __init__(
        self,
        card_store: CardStore,
        *,
        matches: bool = True,
        header: str = "Test Role · ACME · Hamburg",
        summary: str = "A test role description.",
        dedup_store: DeduplicationStore | None = None,
    ) -> None:
        self._card_store = card_store
        self._matches = matches
        self._header = header
        self._summary = summary
        self._dedup_store = dedup_store

    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome:
        listing_id, stub, body = items[0]
        if self._matches:
            self._card_store.put(
                listing_id,
                CardExtract(header=self._header, summary=self._summary),
            )
            if self._dedup_store is not None:
                self._dedup_store.mark_matched(listing_id, stub)
            return _matched_outcome(items)
        return _rejected_outcome(items)


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
    # search-terms present so load_search_terms passes, but triage files are
    # missing â†' PromptError on load_prompts
    config_path = _write_config(tmp_path, with_user_info_files=False)
    st_dir = tmp_path / "user-info" / "search-terms"
    st_dir.mkdir(parents=True, exist_ok=True)
    (st_dir / "keywords.md").write_text("- python\n", encoding="utf-8")

    with pytest.raises(PromptError):
        run(
            config_path,
            # extractor=None so load_prompts() is called
            dedup_store=MagicMock(),
        )


def test_dedup_store_error_propagates(tmp_path: Path) -> None:
    (tmp_path / ".runtime-data").mkdir()
    (tmp_path / ".runtime-data" / "seen.json").write_text(
        "not-valid-json", encoding="utf-8"
    )
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

    class _FailInitFile(DailyResultsFile):
        def ensure_initialized(self) -> None:
            raise ResultsFileError("cannot write")

    monkeypatch.setattr(
        "application_pipeline.orchestrator.DailyResultsFile", _FailInitFile
    )

    with pytest.raises(ResultsFileError):
        run(
            config_path,
            extractor=_stub_extractor(),
            dedup_store=MagicMock(),
        )


# ---------------------------------------------------------------------------
# Unknown parser_type â†' WARNING + excluded, run continues
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


def test_integration_discover_and_enrich(tmp_path: Path) -> None:
    """2 keywords Ã— 1 location, 3 stubs each â†' discovered==6, skipped==0, written==5 (capped by judge_top_n)."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python", "django"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    card_store = _make_card_store(tmp_path)

    summary = run(
        config_path,
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.discovered == 6
    assert summary.skipped == 0
    assert summary.written == 5  # judge_top_n caps at 5


def test_integration_all_skipped_when_preseeded(tmp_path: Path) -> None:
    """Pre-seed all 6 URLs â†' discovered==6, skipped==6, written==0."""
    seen_path = tmp_path / ".seen.json"
    seen_data = {
        str(i + 1): {
            "urls": [url],
            "company_lc": None,
            "title_lc": None,
            "location_lc": None,
            "status": "out_of_domain",
            "first_seen": "2024-01-01",
        }
        for i, url in enumerate(_STUB_URLS)
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
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
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
        parser_registry=lambda _: _TrackingParser,  # type: ignore[return-value, arg-type]
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
    """2 url_hits + 1 tuple_hit + 4 post-enrich run_hits keep terminal dedup counts accurate."""
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

    dedup = MagicMock()
    # 4 misses, 2 url_hits, 1 tuple_hit at pre-discover; the 4 misses each
    # trigger a post-enrich is_seen call that returns run_hit (no new match).
    dedup.is_seen.side_effect = [
        dedup_module.RunScopedSeenResult("miss", 0),  # stub0 pre-discover
        dedup_module.RunScopedSeenResult(
            "run_hit", 0
        ),  # stub0 post-enrich (no new match)
        dedup_module.RunScopedSeenResult("miss", 0),  # stub1 pre-discover
        dedup_module.RunScopedSeenResult("run_hit", 0),  # stub1 post-enrich
        dedup_module.RunScopedSeenResult(
            "url_hit", 0
        ),  # stub2 pre-discover (dedup drop, no post-enrich call)
        dedup_module.RunScopedSeenResult("miss", 0),  # stub3 pre-discover
        dedup_module.RunScopedSeenResult("run_hit", 0),  # stub3 post-enrich
        dedup_module.RunScopedSeenResult(
            "tuple_hit", 0
        ),  # stub4 pre-discover (dedup drop, no post-enrich call)
        dedup_module.RunScopedSeenResult(
            "url_hit", 0
        ),  # stub5 pre-discover (dedup drop, no post-enrich call)
        dedup_module.RunScopedSeenResult("miss", 0),  # stub6 pre-discover
        dedup_module.RunScopedSeenResult("run_hit", 0),  # stub6 post-enrich
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
            parser_registry=lambda _: _SevenStubParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup,
        )

    assert summary.dedup_url_hits == 2
    assert summary.dedup_tuple_hits == 1
    assert summary.dedup_run_hits == 4
    assert summary.dedup_misses == 0
    assert summary.skipped == summary.dedup_url_hits + summary.dedup_tuple_hits

    run_complete = next(
        r.getMessage() for r in caplog.records if "run complete:" in r.getMessage()
    )
    assert "dedup_url_hits=2" in run_complete
    assert "dedup_tuple_hits=1" in run_complete
    assert "dedup_run_hits=4" in run_complete
    assert "dedup_misses=0" in run_complete


# ---------------------------------------------------------------------------
# Integration: in-run dedup (issue #225)
# ---------------------------------------------------------------------------


def test_in_run_dedup_same_url_across_two_queries(tmp_path: Path) -> None:
    """Same URL yielded by two different ParserQuerys â†' second yield is run_hit, enricher called once."""
    enrich_calls: list[str] = []
    duplicate_url = "https://dup.example/job"

    class _DupParser(_StubParserBase):
        def __enter__(self) -> "_DupParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=duplicate_url, title="Job", source="stub")]

    card_store = _make_card_store(tmp_path)

    class _TrackingEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            enrich_calls.append(stub.url)
            return super().enrich(items)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python", "django"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_TrackingEnricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _DupParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.dedup_run_hits == 2
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

    dedup = MagicMock()
    dedup.is_seen.return_value = dedup_module.RunScopedSeenResult("url_hit", 0)
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
        parser_registry=lambda _: _HitOnlyParser,  # type: ignore[return-value, arg-type]
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

    card_store = _make_card_store(tmp_path)

    class _TrackingEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            enrich_calls.append(stub.url)
            return super().enrich(items)

    dedup = MagicMock()
    # 80 url_hits, 1 miss at pre-discover; the miss triggers a post-enrich call (run_hit = no new match).
    dedup.is_seen.side_effect = [
        dedup_module.RunScopedSeenResult("url_hit", 0)
    ] * 80 + [
        dedup_module.RunScopedSeenResult("miss", 0),
        dedup_module.RunScopedSeenResult("run_hit", 0),
    ]
    _wire_run_scope(dedup)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_TrackingEnricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _TrailingParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup,
    )

    assert unseen_url in enrich_calls, "trailing unseen stub must be enriched"
    assert summary.discovered == 81
    assert summary.dedup_url_hits == 80
    assert summary.dedup_run_hits == 1
    assert summary.dedup_misses == 0


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
            parser_registry=lambda _: _DupParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        )

    run_complete = next(
        r.getMessage() for r in caplog.records if "run complete:" in r.getMessage()
    )
    assert "dedup_run_hits=2" in run_complete


@pytest.mark.parametrize(
    ("dedup_kind", "seed_original", "counter_key"),
    [
        (
            "url_hit",
            lambda dedup_store, stub: dedup_store.mark_out_of_domain(stub),
            "dedup_url_hits",
        ),
        (
            "tuple_hit",
            lambda dedup_store, stub: dedup_store.mark_out_of_domain(
                PositionStub(
                    url="https://post-discover.example/original",
                    title=stub.title,
                    source=stub.source,
                    company=stub.company,
                    location=stub.location,
                )
            ),
            "dedup_tuple_hits",
        ),
        (
            "fuzzy_hit",
            lambda dedup_store, stub: dedup_store.mark_out_of_domain(
                PositionStub(
                    url="https://post-discover.example/original",
                    title="Senior Lead Platform Backend Engineer",
                    source=stub.source,
                    company=stub.company,
                    location=stub.location,
                )
            ),
            "dedup_fuzzy_hits",
        ),
    ],
)
def test_post_discover_dedup_skip_stays_local_and_records_actual_hit_kind(
    tmp_path: Path,
    dedup_kind: str,
    seed_original: Callable[[DeduplicationStore, PositionStub], None],
    counter_key: str,
) -> None:
    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    display = FakeStatusDisplay()
    extractor = _stub_extractor()
    stub = PositionStub(
        url="https://post-discover.example/alias",
        title="Lead Platform Backend Engineer",
        source="stub",
        company="Acme",
        location="Hamburg",
    )

    class _DedupSkipParser(_StubParserBase):
        def __enter__(self) -> "_DedupSkipParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [stub]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            raise AssertionError(
                f"post-discover {dedup_kind} must stop before Parser.enrich()"
            )

    card_store = _make_card_store(tmp_path)
    dedup_store = dedup_module.load(tmp_path / ".seen.json", card_store=card_store)
    seed_original(dedup_store, stub)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=extractor,
        card_store=card_store,
        parser_registry=lambda _: _DedupSkipParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_store,
        status_display=display,
        run_log=run_log,
    )

    assert summary.discovered == 1
    assert summary.classify_items == 0
    assert summary.written == 0
    assert extractor.classify_relevance.call_count == 0

    gates_bodies = display.body_updates_for("parser bundesagentur api gates")
    assert any("1 dedup" in body for body in gates_bodies)

    rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "dedup.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    run_complete_rows = [row for row in rows if row.get("event") == "run_complete"]
    assert len(run_complete_rows) == 1
    row = run_complete_rows[0]
    assert row[counter_key] == 1
    assert row["dedup_url_hits"] == (1 if counter_key == "dedup_url_hits" else 0)
    assert row["dedup_tuple_hits"] == (1 if counter_key == "dedup_tuple_hits" else 0)
    assert row["dedup_fuzzy_hits"] == (1 if counter_key == "dedup_fuzzy_hits" else 0)
    assert row["dedup_run_hits"] == 0
    assert row["dedup_misses"] == 0
    assert row["judge_resumed"] == 0


def test_post_discover_run_hit_stays_local_and_updates_parser_gates_row(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    display = FakeStatusDisplay()
    extractor = _stub_extractor()
    enrich_calls: list[str] = []
    duplicate_url = "https://post-discover.example/run-hit"

    class _RunHitParser(_StubParserBase):
        def __enter__(self) -> "_RunHitParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=duplicate_url, title="Backend Engineer", source="stub")
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            enrich_calls.append(stub.url)
            return super().enrich(stub)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python", "django"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=extractor,
        card_store=_make_card_store(tmp_path),
        parser_registry=lambda _: _RunHitParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
        run_log=run_log,
    )

    assert summary.discovered == 2
    assert summary.classify_items == 1
    assert extractor.classify_relevance.call_count == 1
    assert enrich_calls == [duplicate_url]

    gates_bodies = display.body_updates_for("parser bundesagentur api gates")
    assert any("1 dedup" in body for body in gates_bodies)

    rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "dedup.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    run_complete_rows = [row for row in rows if row.get("event") == "run_complete"]
    assert len(run_complete_rows) == 1
    row = run_complete_rows[0]
    assert row["dedup_run_hits"] == 2
    assert row["dedup_misses"] == 0
    assert row["dedup_url_hits"] == 0
    assert row["dedup_tuple_hits"] == 0
    assert row["dedup_fuzzy_hits"] == 0
    assert row["judge_resumed"] == 0


def test_parser_intake_miss_paths_keep_single_miss_in_run_summary_and_dedup_run_complete(
    tmp_path: Path,
) -> None:
    import httpx

    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    urls = {
        "prefilter": "https://parser-intake-miss.example/prefilter",
        "enrich_failed": "https://parser-intake-miss.example/enrich-failed",
        "oversized": "https://parser-intake-miss.example/oversized",
        "transient": "https://parser-intake-miss.example/transient",
        "forwarded": "https://parser-intake-miss.example/forwarded",
    }
    forwarded_urls: list[str] = []

    class _MissPathsParser(_StubParserBase):
        def __enter__(self) -> "_MissPathsParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=urls["prefilter"],
                    title="Excluded Backend Role",
                    source="stub",
                ),
                PositionStub(
                    url=urls["enrich_failed"],
                    title="Backend Engineer",
                    source="stub",
                ),
                PositionStub(
                    url=urls["oversized"],
                    title="Backend Engineer",
                    source="stub",
                ),
                PositionStub(
                    url=urls["transient"],
                    title="Backend Engineer",
                    source="stub",
                ),
                PositionStub(
                    url=urls["forwarded"],
                    title="Backend Engineer",
                    source="stub",
                ),
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            if stub.url == urls["enrich_failed"]:
                raise EnrichFailedError("native fetch failed")
            if stub.url == urls["oversized"]:
                raise OversizedBodyError(
                    url=stub.url,
                    source=stub.source,
                    body_len=4321,
                )
            if stub.url == urls["transient"]:
                raise httpx.HTTPStatusError(
                    "503 Service Unavailable",
                    request=httpx.Request("GET", stub.url),
                    response=httpx.Response(503),
                )
            forwarded_urls.append(stub.url)
            return EnrichResult(stub=stub, body="body text " + "x" * 91, mode="native")

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
            negative_keywords='["excluded"]',
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _MissPathsParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    assert forwarded_urls == [urls["forwarded"]]
    assert summary.discovered == 5
    assert summary.prefilter_dropped == 1
    assert summary.enrich_failed == 1
    assert summary.classify_items == 1
    assert summary.dedup_misses == 4
    assert summary.dedup_url_hits == 0
    assert summary.dedup_tuple_hits == 0
    assert summary.dedup_run_hits == 1
    assert summary.judge_resumed == 0

    row = _dedup_run_complete_row(logs_dir)
    assert row["dedup_misses"] == 4
    assert row["dedup_url_hits"] == 0
    assert row["dedup_tuple_hits"] == 0
    assert row["dedup_fuzzy_hits"] == 0
    assert row["dedup_run_hits"] == 1
    assert row["judge_resumed"] == 0


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

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    dedup = MagicMock()
    # Both runs: is_seen returns "miss" (in-run set does not carry across runs)
    dedup.is_seen.return_value = dedup_module.RunScopedSeenResult("miss", 0)
    _wire_run_scope(dedup)

    summary1 = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup,
    )
    summary2 = run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
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

_FAKE_ENRICH_HEADER = "Test Role · ACME · Hamburg"
_FAKE_ENRICH_SUMMARY = "A test role."


class _FakeLLMEnricherRejectJob1:
    """Fake LLMEnricher: rejects stub with title 'Job 1'; all others are in-domain.

    Tracks per-call usage: _FAKE_CLASSIFY_USAGE per call.
    """

    def __init__(
        self,
        card_store: CardStore,
        dedup_store: DeduplicationStore | None = None,
    ) -> None:
        self._card_store = card_store
        self._dedup_store = dedup_store

    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome:
        listing_id, stub, body = items[0]
        if stub.title == "Job 1":
            if self._dedup_store is not None:
                self._dedup_store.mark_out_of_domain(listing_id, stub)
            return _rejected_outcome(items)
        self._card_store.put(
            listing_id,
            CardExtract(header=_FAKE_ENRICH_HEADER, summary=_FAKE_ENRICH_SUMMARY),
        )
        if self._dedup_store is not None:
            self._dedup_store.mark_matched(listing_id, stub)
        return _matched_outcome(items)


class _FakeExtractor:
    """Deterministic extractor (v2): judge_top_n only."""

    def judge_top_n(
        self, candidates: list[JudgeCandidate]
    ) -> tuple[list[MatchVerdict], CallUsage]:
        verdicts = []
        for i, c in enumerate(candidates[:5]):
            verdicts.append(MatchVerdict(id=c.id, rank=i + 1))
        return verdicts, _FAKE_JUDGE_USAGE


def test_orchestrator_parser_lifecycle_full_run_smoke(tmp_path: Path) -> None:
    """Full-run smoke keeps only the Orchestrator-to-Parser-Lifecycle wiring surface."""
    seen_path = tmp_path / ".seen.json"
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )
    card_store = _make_card_store(tmp_path)
    dedup_store = dedup_module.load(seen_path)
    run_log = RunLog(logs_dir)

    summary = run(
        config_path,
        llm_enricher=_FakeLLMEnricherRejectJob1(card_store, dedup_store=dedup_store),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_store,
        run_log=run_log,
    )

    assert summary.discovered == 6
    assert summary.skipped == 0
    assert summary.prefilter_dropped == 1
    assert summary.classifier_dropped == 1
    assert summary.written == 4

    parser_rows = [
        json.loads(line)
        for line in (logs_dir / "parser" / "bundesagentur_api.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["event"] for row in parser_rows] == [
        "parser started",
        "query_started",
        "query_ended",
    ]
    run_log_content = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert "SUMMARY OF SESSION" in run_log_content
    assert "=== parser_bundesagentur_api" in run_log_content
    assert "queries_done=1" in run_log_content

    # .seen.json: 2 out_of_domain, 4 selected_by_judge
    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    out_of_domain = [
        url
        for rec in seen_data.values()
        if rec["status"] == "out_of_domain"
        for url in rec.get("urls", [])
    ]
    selected = [
        url
        for rec in seen_data.values()
        if rec["status"] == "selected_by_judge"
        for url in rec.get("urls", [])
    ]
    assert len(out_of_domain) == 2
    assert len(selected) == 4
    assert _PF_REJECTED_LLM_URL in out_of_domain
    assert _CLS_REJECTED_LLM_URL in out_of_domain

    # 4 card H1s in the daily results file (v2 format: # **rank:** header)
    content = _read_all_results(results_dir)
    cards = re.findall(r"^# \*\*\d+:\*\* .+", content, re.MULTILINE)
    assert len(cards) == 4


def test_no_judge_skips_judge_no_daily_file_listings_remain_matched(
    tmp_path: Path,
) -> None:
    """no_judge=True: classify runs normally, judge skipped, no daily file, matched stays matched."""
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
    card_store = _make_card_store(tmp_path)

    judge_called = False

    class _TrackingExtractor:
        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            nonlocal judge_called
            judge_called = True
            return [], _ZERO_USAGE

    summary = run(
        config_path,
        llm_enricher=_FakeLLMEnricherRejectJob1(
            card_store, dedup_store=dedup_module.load(seen_path)
        ),
        extractor=_TrackingExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
        no_judge=True,
    )

    assert not judge_called, "judge_top_n must not be called when no_judge=True"
    assert not _read_all_results(results_dir), (
        "no daily results file when no_judge=True"
    )

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    statuses = {rec["status"] for rec in seen_data.values()}
    assert "selected_by_judge" not in statuses, (
        "no listing should become selected_by_judge when no_judge=True"
    )
    matched = [rec for rec in seen_data.values() if rec["status"] == "matched"]
    assert len(matched) == 4, "4 classified listings should remain matched"

    assert summary.discovered == 6
    assert summary.classifier_dropped == 1
    assert summary.prefilter_dropped == 1
    assert summary.written == 0  # no judge ran, so nothing written to daily file


def test_integration_dedup_skip_rerun(tmp_path: Path) -> None:
    """Second run on same tmp_path â†' all 6 skipped, tier files unchanged."""
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
    card_store = _make_card_store(tmp_path)

    def _make_run() -> RunSummary:
        dedup_store = dedup_module.load(seen_path)
        return run(
            config_path,
            llm_enricher=_FakeLLMEnricherRejectJob1(
                card_store, dedup_store=dedup_store
            ),
            extractor=_FakeExtractor(),
            card_store=card_store,
            parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_store,
        )

    first = _make_run()
    assert first.written == 4

    numbers_after_first = re.findall(
        r"^# \*\*(\d+):\*\*", _read_all_results(results_dir), re.MULTILINE
    )

    second = _make_run()
    assert second.discovered == 6
    assert second.skipped == 6
    assert second.prefilter_dropped == 0
    assert second.classifier_dropped == 0
    assert second.written == 0

    # No new position entries added on second run
    numbers_after_second = re.findall(
        r"^# \*\*(\d+):\*\*", _read_all_results(results_dir), re.MULTILINE
    )
    assert numbers_after_second == numbers_after_first


def test_classify_precedes_judge(tmp_path: Path) -> None:
    """All enrich calls complete before judge_top_n is called."""
    call_log: list[str] = []
    card_store = _make_card_store(tmp_path)

    class _InstrumentedEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            call_log.append("enrich")
            return super().enrich(items)

    class _InstrumentedExtractor:
        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            call_log.append("judge_top_n")
            return [
                MatchVerdict(id=c.id, rank=i + 1) for i, c in enumerate(candidates[:5])
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

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    run(
        config_path,
        llm_enricher=_InstrumentedEnricher(card_store),
        extractor=_InstrumentedExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _MultiStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert call_log.count("enrich") == 5
    assert call_log.count("judge_top_n") == 1
    # All enrich calls before the judge_top_n call
    last_enrich = max(i for i, c in enumerate(call_log) if c == "enrich")
    first_judge = min(i for i, c in enumerate(call_log) if c == "judge_top_n")
    assert last_enrich < first_judge, (
        f"enrich and judge_top_n calls interleaved: {call_log}"
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


def _two_stub_config(tmp_path: Path) -> Path:
    return _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )


def test_extractor_error_on_classify_leaves_position_unseen(tmp_path: Path) -> None:
    """ExtractorError from llm_enricher.enrich(): the failing position is not marked seen; run continues."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    call_count = [0]

    class _FailFirstEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            call_count[0] += 1
            if call_count[0] == 1:
                raise ExtractorError("classify boom")
            return super().enrich(items)

    summary = run(
        _two_stub_config(tmp_path),
        llm_enricher=_FailFirstEnricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    # First position errored, second succeeded (1 position written)
    assert summary.errored == 1
    assert summary.written == 1

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    # First position must NOT be in seen store (left un-seen for retry)
    assert not any(_ERR_URLS[0] in r.get("urls", []) for r in seen_data.values())


def test_extractor_error_on_judge_leaves_status_matched(tmp_path: Path) -> None:
    """ExtractorError on judge_top_n: all positions stay matched (not selected_by_judge), no daily file written."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    ext = MagicMock()
    ext.judge_top_n.side_effect = ExtractorError("judge boom")

    summary = run(
        _two_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(
            card_store, dedup_store=dedup_module.load(seen_path)
        ),
        extractor=ext,
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.written == 0

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert (
        next(
            r["status"] for r in seen_data.values() if _ERR_URLS[0] in r.get("urls", [])
        )
        == "matched"
    )
    assert (
        next(
            r["status"] for r in seen_data.values() if _ERR_URLS[1] in r.get("urls", [])
        )
        == "matched"
    )


def test_match_judge_failure_writes_one_failure_report(tmp_path: Path) -> None:
    """A non-quota Match Judge failure writes one Failure Report at stage judge_top_n."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    ext = MagicMock()
    ext.judge_top_n.side_effect = ExtractorError("judge boom")

    run(
        _one_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(
            card_store, dedup_store=dedup_module.load(seen_path)
        ),
        extractor=ext,
        card_store=card_store,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    reports = sorted((tmp_path / ".runtime-data" / "failures").glob("*.md"))
    assert len(reports) == 1

    body = reports[0].read_text(encoding="utf-8")
    assert "**Stage:** judge_top_n" in body
    assert "ExtractorError" in body
    assert "judge boom" in body


def test_parser_error_on_enrich_skips_stub_and_increments_metric(
    tmp_path: Path,
) -> None:
    """LLMEnricher.enrich() returns None → enrich_failed increments, URL absent from seen.json, other stubs proceed."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

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

    class _NoneForSecondEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            if stub.url == _ERR_URLS[1]:
                return _retryable_outcome(items)
            return super().enrich(items)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_NoneForSecondEnricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _EnrichFailParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.enrich_failed == 1
    assert summary.written == 2

    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    assert not any(_ERR_URLS[1] in r.get("urls", []) for r in seen_data.values()), (
        "verdict=None must not write to seen.json — URL stays unrecorded for retry next run"
    )


def test_per_stub_http_error_on_enrich_increments_enrich_failed_and_continues(
    tmp_path: Path,
) -> None:
    """LLMEnricher.enrich() returns None → enrich_failed increments, URL absent from seen.json, other stubs continue."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    class _ThreeStubParser(_StubParserBase):
        def __enter__(self) -> "_ThreeStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=_ERR_URLS[i], title=f"Job {i}", source="stub")
                for i in range(3)
            ]

    class _HttpErrorEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            if stub.url == _ERR_URLS[1]:
                return _retryable_outcome(items)
            return super().enrich(items)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_HttpErrorEnricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _ThreeStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.enrich_failed == 1
    assert summary.parsers_dead == 0
    assert summary.written == 2
    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    assert not any(_ERR_URLS[1] in r.get("urls", []) for r in seen_data.values()), (
        "verdict=None must not write to seen.json — URL stays unrecorded for retry next run"
    )


def test_parser_fatal_http_error_on_enrich_marks_parser_dead_surviving_parsers_continue(
    tmp_path: Path,
) -> None:
    """Parser raises exception in discover() â†' parsers_dead increments, surviving parsers complete."""
    card_store = _make_card_store(tmp_path)

    class _FatalDiscoverParser(_StubParserBase):
        def __enter__(self) -> "_FatalDiscoverParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            raise RuntimeError("auth: stub_url status=401")
            yield  # pragma: no cover â€" makes this a generator

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

    def _registry(parser_type: str) -> type[Parser] | None:
        if parser_type == "fatal":
            return _FatalDiscoverParser  # type: ignore[return-value, arg-type]
        if parser_type == "healthy":
            return _HealthyParser  # type: ignore[return-value, arg-type]
        return None

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api"), SourceEntry(parser_type="fatal"), SourceEntry(parser_type="healthy")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=_registry,
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.parsers_dead == 1
    assert summary.written == 1


def test_external_redirect_skips_stub_and_increments_counter(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """LLMEnricher returns None → enrich_failed increments, URL absent from seen.json, successful stub written; no WARNING."""
    import logging

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)

    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    class _TwoStubDiscoverParser(_StubParserBase):
        def __enter__(self) -> "_TwoStubDiscoverParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=_ERR_URLS[i], title=f"Job {i}", source="stub")
                for i in range(2)
            ]

    class _SkipFirstEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            if stub.url == _ERR_URLS[0]:
                return _retryable_outcome(items)
            return super().enrich(items)

    with caplog.at_level(logging.WARNING, logger="application_pipeline.orchestrator"):
        summary = run(
            _write_config(
                tmp_path,
                sources='[SourceEntry(parser_type="bundesagentur_api")]',
                keywords='["python"]',
                locations='["Hamburg"]',
                include_remote=False,
            ),
            llm_enricher=_SkipFirstEnricher(card_store),
            extractor=_stub_extractor(),
            card_store=card_store,
            parser_registry=lambda _: _TwoStubDiscoverParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(seen_path),
            run_log=run_log,
        )

    assert summary.enrich_failed == 1
    assert summary.written == 1

    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    assert not any(_ERR_URLS[0] in r.get("urls", []) for r in seen_data.values()), (
        "verdict=None must not write to seen.json — URL stays unrecorded for retry next run"
    )

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records == [], f"unexpected WARNING(s): {warning_records}"


def test_retryable_classify_outcome_logs_matches_none_event(tmp_path: Path) -> None:
    """Retryable per-listing classify outcome: enrich_failed increments, classify_relevance event has matches=None."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)
    card_store = _make_card_store(tmp_path)

    class _OneStubDiscoverParser(_StubParserBase):
        def __enter__(self) -> "_OneStubDiscoverParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://example.com/job/1", title="Job 1", source="stub"
                )
            ]

    class _NoneEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            return _retryable_outcome(items)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_NoneEnricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _OneStubDiscoverParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    assert summary.enrich_failed == 1

    events = [
        json.loads(line)
        for line in (logs_dir / "llm" / "classify_relevance.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    classify_rows = [r for r in events if r.get("event") == "classify_relevance"]
    assert len(classify_rows) == 1
    assert classify_rows[0].get("matches") is None


def test_parser_error_mid_discover_processes_yielded_stubs(tmp_path: Path) -> None:
    """ParserError mid-discover: already-yielded stubs processed, run advances to next combination."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

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

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _MidDiscoverFailParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.discovered == 3
    assert summary.written == 3


# ---------------------------------------------------------------------------
# judge_pending: classifyâ†'judge boundary idempotency (issue #289)
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


def _one_stub_config(tmp_path: Path) -> Path:
    return _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )


def test_judge_failure_leaves_status_matched(tmp_path: Path) -> None:
    """After enrich succeeds and judge_top_n raises ExtractorError, seen store has matched."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    ext = MagicMock()
    ext.judge_top_n.side_effect = ExtractorError("judge boom")

    run(
        _one_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(
            card_store, dedup_store=dedup_module.load(seen_path)
        ),
        extractor=ext,
        card_store=card_store,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert (
        next(
            r["status"] for r in seen_data.values() if _RESUME_URL in r.get("urls", [])
        )
        == "matched"
    )


def test_judge_pending_bypasses_classify_on_rerun(tmp_path: Path) -> None:
    """On rerun, a matched URL (judge_pending) goes to judge pool directly from card_store.

    In v2, judge_pending stubs skip the enrich queue entirely and their card
    is read from the card_store that was populated in the previous run.
    """
    seen_path = tmp_path / ".seen.json"
    enrich_calls: list[str] = []

    seen_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": [_RESUME_URL],
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "matched",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    # Write card store in v2 format so judge_candidates can retrieve the card
    _card = {"header": "ML Engineer · ACME · Hamburg", "summary": "Good ML role."}
    (tmp_path / "extracts.json").write_text(
        json.dumps({"1": _card}),
        encoding="utf-8",
    )
    card_store = load_card_store(tmp_path / "extracts.json")

    class _TrackingEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            enrich_calls.append(stub.url)
            return super().enrich(items)

    judge_candidate_ids: list[int] = []

    class _TrackingExtractor:
        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            for c in candidates:
                judge_candidate_ids.append(c.id)
            return [
                MatchVerdict(id=c.id, rank=i + 1) for i, c in enumerate(candidates[:5])
            ], _ZERO_USAGE

    summary = run(
        _one_stub_config(tmp_path),
        llm_enricher=_TrackingEnricher(card_store),
        extractor=_TrackingExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert enrich_calls == [], "enrich must NOT be called for judge_pending stubs (v2)"
    assert 1 in judge_candidate_ids, "judge_pending stub must reach judge"
    assert summary.written == 1, "judge_pending stub must be written"


def test_judge_pending_success_transitions_to_selected_by_judge(tmp_path: Path) -> None:
    """On rerun, if judge succeeds the URL transitions from matched to selected_by_judge."""
    seen_path = tmp_path / ".seen.json"

    seen_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": [_RESUME_URL],
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "matched",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    # Write card store in v2 format so judge_candidates produces a JudgeCandidate
    _card = {"header": "ML Engineer · ACME · Hamburg", "summary": "Good ML role."}
    (tmp_path / "extracts.json").write_text(json.dumps({"1": _card}), encoding="utf-8")
    card_store = load_card_store(tmp_path / "extracts.json")

    run(
        _one_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert (
        next(
            r["status"] for r in seen_data.values() if _RESUME_URL in r.get("urls", [])
        )
        == "selected_by_judge"
    )


def test_judge_pending_failure_stays_matched(tmp_path: Path) -> None:
    """On rerun, if judge fails again the URL stays matched for the next run."""
    seen_path = tmp_path / ".seen.json"

    seen_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": [_RESUME_URL],
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "matched",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    # Write card store in v2 format so judge_candidates finds the candidate
    _card = {"header": "ML Engineer · ACME · Hamburg", "summary": "Good ML role."}
    (tmp_path / "extracts.json").write_text(json.dumps({"1": _card}), encoding="utf-8")
    card_store = load_card_store(tmp_path / "extracts.json")

    ext = MagicMock()
    ext.judge_top_n.side_effect = ExtractorError("judge boom again")

    run(
        _one_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=ext,
        card_store=card_store,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert (
        next(
            r["status"] for r in seen_data.values() if _RESUME_URL in r.get("urls", [])
        )
        == "matched"
    )


def test_judge_pending_enrich_re_fetches_fresh_page(tmp_path: Path) -> None:
    """In v2, judge_pending stubs use the card from card_store directly to reach judge.

    The card_store holds the header/summary from a previous enrichment; the judge
    is called with those values so the stub reaches judge without re-enrich.
    """
    seen_path = tmp_path / ".seen.json"
    judge_candidate_ids: list[int] = []

    seen_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": [_RESUME_URL],
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "matched",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    # Write card store in v2 format; this is what judge_candidates uses
    _card = {"header": "ML Engineer · ACME · Hamburg", "summary": "Good ML role."}
    (tmp_path / "extracts.json").write_text(
        json.dumps({"1": _card}),
        encoding="utf-8",
    )
    card_store = load_card_store(tmp_path / "extracts.json")

    class _CapturingExtractor:
        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            for c in candidates:
                judge_candidate_ids.append(c.id)
            return [
                MatchVerdict(id=c.id, rank=i + 1) for i, c in enumerate(candidates[:5])
            ], _ZERO_USAGE

    run(
        _one_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_CapturingExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert 1 in judge_candidate_ids, (
        "judge_top_n must be called with the judge_pending stub as a candidate"
    )


def test_judge_pending_enrich_failure_skips_stub_without_seen_write(
    tmp_path: Path,
) -> None:
    """LLMEnricher.enrich() returns None → enrich_failed increments, URL absent from seen.json."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)
    _enrich_failed_url = "https://enrich-fail.example/0"

    class _FailEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            return _retryable_outcome(items)

    class _FailStubParser(_StubParserBase):
        def __enter__(self) -> "_FailStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=_enrich_failed_url, title="ML Engineer", source="stub")
            ]

    summary = run(
        _one_stub_config(tmp_path),
        llm_enricher=_FailEnricher(),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _FailStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.enrich_failed == 1
    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    assert not any(
        _enrich_failed_url in r.get("urls", []) for r in seen_data.values()
    ), (
        "verdict=None must not write to seen.json — URL stays unrecorded for retry next run"
    )


def test_judge_pending_appears_in_run_complete_event(tmp_path: Path) -> None:
    """judge_pending stubs (status=matched) are judged via card_store and reach written."""
    import application_pipeline.parser_log as parser_log

    seen_path = tmp_path / ".seen.json"
    logs_dir = tmp_path / "logs"
    run_log = parser_log.RunLog(logs_dir)

    seen_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": [_RESUME_URL],
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "matched",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    # Write a v2-format extracts.json so _wipe_extracts_if_v1 keeps it
    _card = {"header": "ML Engineer · ACME · Hamburg", "summary": "Good ML role."}
    (tmp_path / "extracts.json").write_text(json.dumps({"1": _card}), encoding="utf-8")
    card_store = load_card_store(tmp_path / "extracts.json")

    summary = run(
        _one_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
        run_log=run_log,
    )

    assert summary.written == 1, "judge_pending stub must be written"


def test_judge_pending_keeps_judge_resumed_in_run_summary_and_dedup_run_complete(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)

    seen_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": [_RESUME_URL],
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "matched",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "extracts.json").write_text(
        json.dumps(
            {
                "1": {
                    "header": "ML Engineer · ACME · Hamburg",
                    "summary": "Good ML role.",
                }
            }
        ),
        encoding="utf-8",
    )
    card_store = load_card_store(tmp_path / "extracts.json")

    summary = run(
        _one_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
        run_log=run_log,
    )

    assert summary.written == 1
    assert summary.judge_resumed == 1
    assert summary.dedup_url_hits == 0
    assert summary.dedup_tuple_hits == 0
    assert summary.dedup_run_hits == 0
    assert summary.dedup_misses == 0

    row = _dedup_run_complete_row(logs_dir)
    assert row["judge_resumed"] == 1
    assert row["dedup_url_hits"] == 0
    assert row["dedup_tuple_hits"] == 0
    assert row["dedup_fuzzy_hits"] == 0
    assert row["dedup_run_hits"] == 0
    assert row["dedup_misses"] == 0


def test_judge_pending_judge_failure_stays_matched_on_rerun(
    tmp_path: Path,
) -> None:
    """On rerun, if judge_top_n fails the resumed stub stays matched."""
    seen_path = tmp_path / ".seen.json"

    seen_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": [_RESUME_URL],
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "matched",
                    "first_seen": "2026-05-17",
                }
            }
        ),
        encoding="utf-8",
    )
    # Write a v2-format extracts.json so _wipe_extracts_if_v1 keeps it
    _card = {"header": "ML Engineer · ACME · Hamburg", "summary": "Good ML role."}
    (tmp_path / "extracts.json").write_text(json.dumps({"1": _card}), encoding="utf-8")
    card_store = load_card_store(tmp_path / "extracts.json")

    ext = MagicMock()
    ext.judge_top_n.side_effect = ExtractorError("judge boom")

    run(
        _one_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=ext,
        card_store=card_store,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert (
        next(
            r["status"] for r in seen_data.values() if _RESUME_URL in r.get("urls", [])
        )
        == "matched"
    )


# ---------------------------------------------------------------------------
# Threading: PARSER_DEAD (issue #112)
# ---------------------------------------------------------------------------


def test_parser_thread_dead_run_completes(tmp_path: Path) -> None:
    """Uncaught exception in parser thread â†' parsers_dead==1, run completes (no hang)."""

    class _DeadParser(_StubParserBase):
        def __enter__(self) -> "_DeadParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            raise RuntimeError("unexpected crash")
            yield  # pragma: no cover â€" makes this a generator

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _DeadParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.parsers_dead == 1
    assert summary.discovered == 0
    assert summary.written == 0


def test_parser_thread_dead_surviving_parsers_continue(tmp_path: Path) -> None:
    """One dead parser + one healthy parser â†' dead counted, healthy stubs written."""

    class _DeadParser(_StubParserBase):
        def __enter__(self) -> "_DeadParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            raise RuntimeError("boom")
            yield  # pragma: no cover

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

    def _registry(parser_type: str) -> type[Parser] | None:
        if parser_type == "dead":
            return _DeadParser  # type: ignore[return-value, arg-type]
        if parser_type == "healthy":
            return _HealthyParser  # type: ignore[return-value, arg-type]
        return None

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api"), SourceEntry(parser_type="dead"), SourceEntry(parser_type="healthy")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    card_store = _make_card_store(tmp_path)

    summary = run(
        config_path,
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=_registry,
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.parsers_dead == 1
    assert summary.discovered == 1
    assert summary.written == 1


def test_append_failure_exits_nonzero_position_not_marked_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ResultsFileError from commit: run raises (non-zero exit), position NOT marked seen."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    class _FailCommitFile(DailyResultsFile):
        def commit(
            self, *, rank: int, header: str, summary: str, url: str, body: str
        ) -> None:
            raise ResultsFileError("disk full")

    monkeypatch.setattr(
        "application_pipeline.orchestrator.DailyResultsFile", _FailCommitFile
    )

    with pytest.raises(ResultsFileError):
        run(
            _two_stub_config(tmp_path),
            llm_enricher=_make_fake_llm_enricher(card_store),
            extractor=_stub_extractor(),
            card_store=card_store,
            parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(seen_path),
        )

    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    # No position must be marked kept
    kept = [
        url
        for rec in seen_data.values()
        if rec.get("status") == "selected_by_judge"
        for url in rec.get("urls", [])
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
    card_store = _make_card_store(tmp_path)

    run(
        config_path,
        llm_enricher=_FakeLLMEnricherRejectJob1(card_store),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
        run_log=run_log,
    )

    events_file = logs_dir / "pipeline" / "orchestrator.events.jsonl"
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

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python", "django"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _DupParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_file = logs_dir / "pipeline" / "orchestrator.events.jsonl"
    rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    run_complete_rows = [r for r in rows if r.get("event") == "run_complete"]
    assert len(run_complete_rows) == 1
    assert run_complete_rows[0]["dedup_run_hits"] == 2


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

    class _CrashingEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            raise RuntimeError("unexpected crash escaping main path")

    card_store = _make_card_store(tmp_path)

    with pytest.raises(RuntimeError):
        run(
            config_path,
            llm_enricher=_CrashingEnricher(),
            extractor=_stub_extractor(),
            card_store=card_store,
            parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        )

    # Daily file should not contain any card content (it's initialized empty)
    dated_file = results_dir / f"{today}.md"
    if dated_file.exists():
        content = dated_file.read_text(encoding="utf-8")
        assert re.findall(r"^# \*\*\d+:\*\* .+", content, re.MULTILINE) == [], (
            "no cards must be written when run crashes"
        )


# ---------------------------------------------------------------------------
# Failure Report (issue #117)
# ---------------------------------------------------------------------------


def test_fatal_error_writes_failure_report_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DedupStoreError at startup â†' failure report written, stage=orchestrator, exit 1."""
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

    failures_dir = tmp_path / "application-pipeline" / ".runtime-data" / "failures"
    reports = list(failures_dir.glob("*.md"))
    assert len(reports) == 1, f"expected one failure report, got {reports}"

    body = reports[0].read_text(encoding="utf-8")
    assert "orchestrator" in body
    assert "startup failed" in body  # log tail captured before exception propagated


def test_results_write_error_propagates_from_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ResultsFileError from commit in judge worker propagates from run()."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )
    card_store = _make_card_store(tmp_path)

    class _FailCommitFile(DailyResultsFile):
        def commit(
            self, *, rank: int, header: str, summary: str, url: str, body: str
        ) -> None:
            raise ResultsFileError("disk full")

    monkeypatch.setattr(
        "application_pipeline.orchestrator.DailyResultsFile", _FailCommitFile
    )

    with pytest.raises(ResultsFileError):
        run(
            config_path,
            llm_enricher=_FakeLLMEnricherRejectJob1(card_store),
            extractor=_FakeExtractor(),
            card_store=card_store,
            parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        )


def test_parser_log_records_enrich_failed_redirect_and_dead(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """In v2: enrich_failed and _ParserDead each produce an entry in the parser log; no WARNING/ERROR on stderr; SUMMARY includes counters."""
    import logging

    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)
    card_store = _make_card_store(tmp_path)

    _STUB_URLS = [
        "https://stub.example/0",
        "https://stub.example/1",
    ]

    class _TwoEventParser(_StubParserBase):
        def __enter__(self) -> "_TwoEventParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            yield PositionStub(
                url=_STUB_URLS[0], title="Job 0", source="bundesagentur_api"
            )
            yield PositionStub(
                url=_STUB_URLS[1], title="Job 1", source="bundesagentur_api"
            )
            raise RuntimeError("thread crashed")

    class _EnrichFailEnricher:
        """Enricher that returns None for Job 0 (enrich_failed) and in-domain for Job 1."""

        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            if stub.url == _STUB_URLS[0]:
                return _retryable_outcome(items)
            card_store.put(
                listing_id,
                CardExtract(header=_FAKE_ENRICH_HEADER, summary=_FAKE_ENRICH_SUMMARY),
            )
            return _matched_outcome(items)

    with caplog.at_level(logging.WARNING, logger="application_pipeline.orchestrator"):
        summary = run(
            _write_config(
                tmp_path,
                sources='[SourceEntry(parser_type="bundesagentur_api")]',
                keywords='["python"]',
                locations='["Hamburg"]',
                include_remote=False,
            ),
            llm_enricher=_EnrichFailEnricher(),
            extractor=_stub_extractor(),
            card_store=card_store,
            parser_registry=lambda _: _TwoEventParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
            run_log=run_log,
        )

    assert summary.enrich_failed == 1
    assert summary.parsers_dead == 1

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records == [], f"unexpected WARNING/ERROR(s): {warning_records}"

    events_file = logs_dir / "parser" / "bundesagentur_api.events.jsonl"
    assert events_file.exists(), "events log file must be created"

    run_log_content = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert "traceback" in run_log_content
    # enrich_failed is tracked globally (summary.enrich_failed == 1 above) but the
    # per-parser SUMMARY is written before enrich threads complete, so the file-level
    # counter stays 0. Check the RunSummary return value instead (already asserted above).
    assert "parsers_dead=1" in run_log_content


# ---------------------------------------------------------------------------
# Orchestrator: classify_relevance logging (v2 enricher path)
# ---------------------------------------------------------------------------


class _WarnParser(_StubParserBase):
    """Returns one stub; used in tests for llm_enricher classify_relevance logging."""

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


def test_unparseable_date_warning_routed_to_parser_log(tmp_path: Path) -> None:
    """In v2, classify_relevance events are written to llm_classify_relevance.events.jsonl on successful enrichment."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "synched" / "logs"
    run_log = parser_log.RunLog(logs_dir)
    card_store = _make_card_store(tmp_path)

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="jobs_beim_staat_html")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )

    summary = run(
        config_path,
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _WarnParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_file = logs_dir / "llm" / "classify_relevance.events.jsonl"
    assert events_file.exists(), "classify_relevance events must be written"
    events_rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        row.get("event") == "classify_relevance" and row.get("matches") is True
        for row in events_rows
    ), "classify_relevance matches=True event must appear in events log"
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
            parser_registry=lambda _: _WarnParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
            run_log=run_log,
        )

    assert not any("unparseable_date" in record.message for record in caplog.records), (
        "unparseable_date must not appear in logging output"
    )


# ---------------------------------------------------------------------------
# Batched classify pipeline â€" new tests for issue #187
# ---------------------------------------------------------------------------


def _batch_size_config(tmp_path: Path, batch_size: int = 1) -> Path:
    return _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        claude_classify_batch_size=batch_size,
    )


def test_four_positions_each_get_solo_classify_call(tmp_path: Path) -> None:
    """4 positions â†' llm_enricher.enrich() called 4 times, once per position."""
    call_count = [0]
    card_store = _make_card_store(tmp_path)

    class _CountingEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            call_count[0] += 1
            card_store.put(
                listing_id,
                CardExtract(header=_FAKE_ENRICH_HEADER, summary=_FAKE_ENRICH_SUMMARY),
            )
            return _matched_outcome(items)

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

    summary = run(
        _batch_size_config(tmp_path),
        llm_enricher=_CountingEnricher(),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _FourStubParser,  # type: ignore[return-value, arg-type]
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

    display = FakeStatusDisplay()

    run(
        _batch_size_config(tmp_path, 1),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _SlowTwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    first_classify_idx = next(
        i
        for i, c in enumerate(display.calls)
        if c.method == "update_body" and c.name == "llm classify relevance"
    )
    parser_done_idx = next(
        i
        for i, c in enumerate(display.calls)
        if c.method == "update_phase"
        and c.name.startswith("parser ")
        and c.kwargs.get("phase") == "done"
    )

    assert first_classify_idx < parser_done_idx, (
        "classify_relevance update_body must precede parser-done update_phase"
    )


def test_classify_thread_six_positions_happy_path(tmp_path: Path) -> None:
    """v2 pipeline happy path: 6 stubs â†' llm_enricher.enrich() Ã— 6, judge caps at 5 written.

    All positions are in-domain and judged.  Asserts set-equality on the
    URLs that appear in daily results and on the 'kept' members of .seen.json.
    """
    seen_path = tmp_path / ".seen.json"
    results_dir = tmp_path / "results"
    card_store = _make_card_store(tmp_path)

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

    summary = run(
        _batch_size_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _SixStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    # 6 positions â†' 6 enrich calls; judge_top_n caps at 5
    assert summary.written == 5
    assert summary.classify_items == 6
    assert summary.classifier_dropped == 0

    # .seen.json: 5 URLs kept (top-5 from judge_top_n)
    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    kept_urls = {
        url
        for rec in seen_data.values()
        if rec["status"] == "selected_by_judge"
        for url in rec.get("urls", [])
    }
    assert len(kept_urls) == 5
    assert kept_urls.issubset(_ALL_URLS)

    # Daily results file: 5 ranked cards rendered (v2 renderer uses header/summary, not URLs)
    content = _read_all_results(results_dir)
    # Count rank headers: "# **1:** ...", "# **2:** ...", etc.
    ranked_cards = [line for line in content.splitlines() if line.startswith("# **")]
    assert len(ranked_cards) == 5


def test_mixed_listing_set_all_classified(tmp_path: Path) -> None:
    """Mixed-language listings are all enriched individually via llm_enricher."""
    call_count = [0]
    card_store = _make_card_store(tmp_path)

    class _CountingEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            call_count[0] += 1
            card_store.put(
                listing_id,
                CardExtract(header=_FAKE_ENRICH_HEADER, summary=_FAKE_ENRICH_SUMMARY),
            )
            return _matched_outcome(items)

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

    summary = run(
        _batch_size_config(tmp_path),
        llm_enricher=_CountingEnricher(),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _FourStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.written == 4
    assert call_count[0] == 4


def test_off_domain_marked_seen_immediately_no_judge(tmp_path: Path) -> None:
    """Rejected real LLM Enricher outcomes mark seen, skip Card writes, and stay out of judge candidates."""
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    _OFF_URL = "https://offdomain.example/0"
    _ON_URL = "https://offdomain.example/1"
    card_store = load_card_store(extracts_path)

    judge_candidate_ids: list[list[int]] = []

    class _TrackingExtractor:
        def classify_relevance(
            self, items: list[ClassifyItem]
        ) -> tuple[list[RelevanceVerdict | None], CallUsage]:
            assert len(items) == 1
            if items[0].title == "Off-domain Job":
                return [RelevanceVerdict(matches=False)], _ZERO_USAGE
            return [
                RelevanceVerdict(
                    matches=True,
                    header=_FAKE_ENRICH_HEADER,
                    summary=_FAKE_ENRICH_SUMMARY,
                )
            ], _ZERO_USAGE

        def judge_top_n(
            self, candidates: "list[JudgeCandidate]"
        ) -> "tuple[list[MatchVerdict], CallUsage]":
            judge_candidate_ids.append([c.id for c in candidates])
            return [
                MatchVerdict(id=c.id, rank=i + 1) for i, c in enumerate(candidates[:5])
            ], _ZERO_USAGE

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

    run_log = RunLog(logs_dir)
    summary = run(
        _batch_size_config(tmp_path),
        extractor=_TrackingExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _TwoLangParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
        run_log=run_log,
    )

    assert summary.classifier_dropped == 1
    assert summary.written == 1
    # judge_top_n called with only the in-domain candidate
    assert len(judge_candidate_ids) == 1
    assert len(judge_candidate_ids[0]) == 1  # only the in-domain candidate
    assert card_store.get(1) is None
    assert card_store.get(2) is not None

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert (
        next(r["status"] for r in seen_data.values() if _OFF_URL in r.get("urls", []))
        == "out_of_domain"
    )
    events = [
        json.loads(line)
        for line in (logs_dir / "llm" / "classify_relevance.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert [
        row["matches"] for row in events if row.get("event") == "classify_relevance"
    ] == [
        False,
        True,
    ]


def test_classify_malformed_position_not_marked_seen(tmp_path: Path) -> None:
    """ExtractorError from llm_enricher.enrich(): the failing position is not marked seen; run continues."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    call_count = [0]

    class _FailFirstEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            call_count[0] += 1
            if call_count[0] == 1:
                from application_pipeline.llm import ExtractorMalformedError

                raise ExtractorMalformedError("bad verdict")
            card_store.put(
                listing_id,
                CardExtract(header=_FAKE_ENRICH_HEADER, summary=_FAKE_ENRICH_SUMMARY),
            )
            return _matched_outcome(items)

    summary = run(
        _two_stub_config(tmp_path),
        llm_enricher=_FailFirstEnricher(),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    # First call failed (1 item errored), second succeeded (1 item written)
    assert summary.errored == 1
    assert summary.written == 1

    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    # First item must NOT be in seen store
    assert not any(_ERR_URLS[0] in r.get("urls", []) for r in seen_data.values())


def test_batch_malformed_classify_failure_stays_at_stage_seam_and_run_continues(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "synched" / "logs"
    run_log = RunLog(logs_dir)
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)
    urls = [f"https://stub.example/batch/{i}" for i in range(3)]

    class _ThreeStubParser(_StubParserBase):
        def __enter__(self) -> "_ThreeStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=url, title=f"Batch Job {i}", source="stub")
                for i, url in enumerate(urls, start=1)
            ]

    class _BatchMalformedThenMatchedEnricher:
        def __init__(self) -> None:
            self.calls = 0

        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            self.calls += 1
            if self.calls == 1:
                raise ExtractorBatchMalformedError("bad batch verdict")
            listing_id, stub, body = items[0]
            card_store.put(
                listing_id,
                CardExtract(header=_FAKE_ENRICH_HEADER, summary=_FAKE_ENRICH_SUMMARY),
            )
            return _matched_outcome(items)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
            claude_classify_batch_size=2,
        ),
        llm_enricher=_BatchMalformedThenMatchedEnricher(),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _ThreeStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
        run_log=run_log,
    )

    assert summary.errored == 2
    assert summary.written == 1
    assert summary.classify_items == 1
    assert card_store.get(3) is not None

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert not any(urls[0] in row.get("urls", []) for row in seen_data.values())
    assert not any(urls[1] in row.get("urls", []) for row in seen_data.values())
    assert any(urls[2] in row.get("urls", []) for row in seen_data.values())

    classify_rows = [
        json.loads(line)
        for line in (logs_dir / "llm" / "classify_relevance.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert classify_rows == [
        {
            "ts": classify_rows[0]["ts"],
            "event": "classify_relevance",
            "status": "error",
            "error": "bad batch verdict",
        },
        {
            "ts": classify_rows[1]["ts"],
            "event": "classify_relevance",
            "matches": True,
        },
    ]

    run_log_text = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert "batches_failed=1" in run_log_text


def test_judge_error_log_includes_forensic_fields(tmp_path: Path) -> None:
    """ExtractorUnreachableError with forensics â†' returncode and stderr_excerpt in judge_top_n log."""
    import application_pipeline.parser_log as pl

    logs_dir = tmp_path / "synched" / "logs"
    run_log = pl.RunLog(logs_dir)
    card_store = _make_card_store(tmp_path)

    ext = MagicMock()
    ext.judge_top_n.side_effect = ExtractorUnreachableError(
        "cli gone", returncode=2, stderr="timeout on judge"
    )

    run(
        _two_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=ext,
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_rows = [
        json.loads(line)
        for line in (logs_dir / "llm" / "judge_top_n.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(row.get("returncode") == 2 for row in events_rows)
    assert any(row.get("stderr_excerpt") == "timeout on judge" for row in events_rows)


# ---------------------------------------------------------------------------
# Prompt loader: only de + en; init materialises only de + en
# ---------------------------------------------------------------------------


def test_prompt_loader_returns_single_template_per_call_site(tmp_path: Path) -> None:
    """load_prompts returns PromptTemplate for classify_relevance and judge_top_n."""
    from application_pipeline.prompts import load_prompts
    from application_pipeline import (
        Config,
        PromptTemplate,
        SourceEntry,
    )

    user_info_dir = tmp_path / "user-info"
    user_info_dir.mkdir()
    triage_dir = user_info_dir / "triage-profile"
    triage_dir.mkdir()
    (triage_dir / "candidate-profile.md").write_text("dev background\n")
    (triage_dir / "gate-criteria.md").write_text("Hamburg, remote\n")

    cfg = Config(
        sources=[SourceEntry(parser_type="bundesagentur_api")],
        locations=["Hamburg"],
        user_info_dir=user_info_dir,
    )
    prompts = load_prompts(cfg)

    assert isinstance(prompts.classify_relevance, PromptTemplate)
    assert isinstance(prompts.judge_top_n, PromptTemplate)


def test_init_materialises_user_info_files(
    tmp_path: Path,
) -> None:
    """init command seeds the three triage-profile template files."""
    from application_pipeline.init_cmd import init

    init(tmp_path)

    triage_files = {
        f.name
        for f in (
            tmp_path / "application-pipeline" / "user-info" / "triage-profile"
        ).glob("*.md")
    }

    assert "candidate-profile.md" in triage_files
    assert "gate-criteria.md" in triage_files


# ---------------------------------------------------------------------------
# RunSummary telemetry (issue #188)
# ---------------------------------------------------------------------------


def test_run_summary_carries_token_and_cost_totals(tmp_path: Path) -> None:
    """RunSummary accumulates judge token/cost totals from _FakeExtractor (v2: classify tokens are zero)."""
    seen_path = tmp_path / ".seen.json"
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
    )
    card_store = _make_card_store(tmp_path)

    summary = run(
        config_path,
        llm_enricher=_FakeLLMEnricherRejectJob1(card_store),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    # 5 items pass prefilter â†' 5 enrich calls (1 off-domain + 4 in-domain = 5 classified)
    assert summary.classify_items == 5
    # In v2, classify usage is zero; judge usage comes from _FakeExtractor._FAKE_JUDGE_USAGE
    assert summary.claude_input_tokens == _FAKE_JUDGE_USAGE.input_tokens
    assert summary.claude_output_tokens == _FAKE_JUDGE_USAGE.output_tokens
    assert summary.claude_cache_read_tokens == _FAKE_JUDGE_USAGE.cache_read_tokens
    assert abs(summary.claude_cost_usd - _FAKE_JUDGE_USAGE.cost_usd) < 1e-9


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
    card_store = _make_card_store(tmp_path)

    run(
        config_path,
        llm_enricher=_FakeLLMEnricherRejectJob1(card_store),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
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
        "matched=",
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
    card_store = _make_card_store(tmp_path)

    run(
        config_path,
        llm_enricher=_FakeLLMEnricherRejectJob1(card_store),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
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
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
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
# StatusDisplay â€" startup row
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
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
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
# StatusDisplay â€" per-parser rows (issue #197)
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
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    assert "parser bundesagentur api" in display.registered_names()
    reg = next(
        c
        for c in display.calls
        if c.method == "register" and c.name == "parser bundesagentur api"
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
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    indexed = [(i, c.method, c.name) for i, c in enumerate(display.calls)]
    startup_reg_idx = next(
        i for i, m, n in indexed if m == "register" and n == "startup"
    )
    parser_reg_idx = next(
        i for i, m, n in indexed if m == "register" and n == "parser bundesagentur api"
    )
    assert parser_reg_idx > startup_reg_idx


def test_parser_row_body_ends_with_done(tmp_path: Path) -> None:
    """Parser phase column is set to 'done' when parser completes; row is not removed."""
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
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    phase_calls = [
        c
        for c in display.calls
        if c.method == "update_phase" and c.name == "parser bundesagentur api"
    ]
    assert phase_calls, "expected update_phase call for parser row"
    assert phase_calls[-1].kwargs["phase"] == "done", (
        f"last phase {phase_calls[-1].kwargs['phase']!r} must be 'done'"
    )
    assert not any(
        c.method == "remove" and c.name == "parser bundesagentur api"
        for c in display.calls
    ), "parser row must not be removed during run"


def test_parser_row_body_tracks_queries_stubs_enriched(tmp_path: Path) -> None:
    """Parser row body uses new counter format: K discovered · F forwarded."""
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
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    bodies = display.body_updates_for("parser bundesagentur api")
    # _StubParser returns 3 stubs per call; 1 keyword × 1 location = 1 query → 3 stubs
    # bundesagentur_api has has_native_enrich=False → enrich_failed counter absent
    final = bodies[-1]
    assert "discovered" in final, f"unexpected body: {final!r}"
    assert "forwarded" in final, f"unexpected body: {final!r}"
    assert "queries" not in final, f"old format still present: {final!r}"
    assert "stubs" not in final, f"old format still present: {final!r}"
    assert "enrich_failed" not in final, (
        f"enrich_failed must be absent without native enrich: {final!r}"
    )


def test_parser_row_body_shows_native_enriched_counter(tmp_path: Path) -> None:
    """Parser row shows M/N enriched for parsers with has_native_enrich=True."""

    class _NativeParser(_StubParserBase):
        has_native_enrich = True

        def __enter__(self) -> "_NativeParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=f"https://native.example/{i}", title=f"Job {i}", source="stub"
                )
                for i in range(3)
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            return EnrichResult(
                stub=stub, body="native body " + "x" * 89, mode="native"
            )

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
        parser_registry=lambda _: _NativeParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    bodies = display.body_updates_for("parser bundesagentur api")
    final = bodies[-1]
    # All 3 stubs enriched natively → forwarded appears in body
    assert "discovered" in final, f"expected 'discovered' in {final!r}"
    assert "forwarded" in final, f"expected 'forwarded' in {final!r}"
    assert "queries" not in final, f"old format still present: {final!r}"


def test_parser_row_body_shows_partial_native_enriched_counter(tmp_path: Path) -> None:
    """Parser row shows M/N enriched with M<N when some stubs fall back."""
    call_count = [0]

    class _MixedParser(_StubParserBase):
        has_native_enrich = True

        def __enter__(self) -> "_MixedParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=f"https://mixed.example/{i}", title=f"Job {i}", source="stub"
                )
                for i in range(3)
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            call_count[0] += 1
            if call_count[0] == 1:
                return EnrichResult(stub=stub, body="body " + "x" * 96, mode="native")
            return EnrichResult(stub=stub, body="body " + "x" * 96, mode="fallback")

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
        parser_registry=lambda _: _MixedParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    bodies = display.body_updates_for("parser bundesagentur api")
    final = bodies[-1]
    # 3 stubs discovered; forwarded counter present in new format
    assert "discovered" in final, f"expected 'discovered' in {final!r}"
    assert "forwarded" in final, f"expected 'forwarded' in {final!r}"
    assert "queries" not in final, f"old format still present: {final!r}"


def test_parser_row_body_shows_dead_on_crash(tmp_path: Path) -> None:
    """Parser phase column is set to 'dead' when parser thread crashes."""

    class _DeadParserForRow(_StubParserBase):
        def __enter__(self) -> "_DeadParserForRow":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery):  # type: ignore[return]
            raise RuntimeError("intentional crash")
            yield  # pragma: no cover

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
        parser_registry=lambda _: _DeadParserForRow,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    phase_calls = [
        c
        for c in display.calls
        if c.method == "update_phase" and c.name == "parser bundesagentur api"
    ]
    assert phase_calls, "expected update_phase call for dead parser row"
    assert phase_calls[-1].kwargs["phase"] == "dead", (
        f"last phase {phase_calls[-1].kwargs['phase']!r} must be 'dead'"
    )
    assert not any(
        c.method == "remove" and c.name == "parser bundesagentur api"
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
        parser_registry=lambda _: _EmptyParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    registered = display.registered_names()
    assert "parser bundesagentur api" in registered
    assert "parser stellen hamburg api" in registered

    order_a = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "parser bundesagentur api"
    )
    order_b = next(
        c.kwargs["order"]
        for c in display.calls
        if c.method == "register" and c.name == "parser stellen hamburg api"
    )
    assert order_a >= 2
    assert order_b >= 2
    assert order_a != order_b


# ---------------------------------------------------------------------------
# Status Display: dedup and prefilter rows
# ---------------------------------------------------------------------------


def test_dedup_and_prefilter_rows_not_registered(tmp_path: Path) -> None:
    """pipeline_dedup and pipeline_prefilter rows are retired; not registered."""
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
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    registered = display.registered_names()
    assert "pipeline_dedup" not in registered
    assert "pipeline_prefilter" not in registered
    assert "pipeline_freshness" not in registered
    assert "pipeline_content" not in registered


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


def test_classify_and_judge_rows_registered(tmp_path: Path) -> None:
    """classify_relevance row is registered; judge_match row is retired."""
    display = FakeStatusDisplay()

    run(
        _write_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
        status_display=display,
    )

    assert "llm classify relevance" in display.registered_names()
    assert "llm_judge_match" not in display.registered_names()


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
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    assert not any(
        c.method == "remove" and c.name == "llm classify relevance"
        for c in display.calls
    ), "classify_relevance row must not be removed during run"


def test_classify_row_transitions_to_done_after_workers_finish(
    tmp_path: Path,
) -> None:
    """classify_relevance row phase transitions to 'done' after all classify workers join."""
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
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    phase_updates = [
        c
        for c in display.calls
        if c.method == "update_phase" and c.name == "llm classify relevance"
    ]
    assert phase_updates, (
        "expected at least one phase update for 'llm classify relevance'"
    )
    assert phase_updates[-1].kwargs["phase"] == "done", (
        "last phase update for classify row must be 'done'"
    )


def test_classify_row_done_when_no_sources(tmp_path: Path) -> None:
    """classify_relevance row transitions to 'done' even when there are no sources."""
    display = FakeStatusDisplay()

    run(
        _write_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: None,
        dedup_store=MagicMock(),
        status_display=display,
    )

    phase_updates = [
        c
        for c in display.calls
        if c.method == "update_phase" and c.name == "llm classify relevance"
    ]
    assert phase_updates, (
        "expected phase update for 'llm classify relevance' even with no sources"
    )
    assert phase_updates[-1].kwargs["phase"] == "done", (
        "classify row must show 'done' after classify shutdown even with no sources"
    )


# ---------------------------------------------------------------------------
# Non-quota abort (issue #217)
# ---------------------------------------------------------------------------


def test_non_quota_worker_exception_writes_failure_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RuntimeError from judge worker â†' failure report written, exit 1."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "application-pipeline").mkdir()
    _write_config(tmp_path / "application-pipeline")
    monkeypatch.setattr("sys.argv", ["app", "run"])

    class _AbortingExtractor:
        def classify_relevance(self, item: object) -> object:
            # Satisfies LLMExtractor Protocol check; never called (LLMEnricher is monkeypatched)
            raise NotImplementedError

        def judge_top_n(
            self, candidates: "list[JudgeCandidate]"
        ) -> "tuple[list[MatchVerdict], CallUsage]":
            raise RuntimeError("disk full")

    class _InDomainEnricher:
        """Receives the orchestrator's card_store via **kw and writes to it directly."""

        def __init__(self, *, card_store: "CardStore", **_kw: object) -> None:
            self._card_store = card_store

        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            self._card_store.put(
                listing_id,
                CardExtract(header=_FAKE_ENRICH_HEADER, summary=_FAKE_ENRICH_SUMMARY),
            )
            return _matched_outcome(items)

    monkeypatch.setattr(
        "application_pipeline.orchestrator.ClaudeExtractor",
        lambda *a, **kw: _AbortingExtractor(),
    )
    monkeypatch.setattr(
        "application_pipeline.orchestrator.LLMEnricher",
        _InDomainEnricher,
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

    monkeypatch.setattr(
        "application_pipeline.orchestrator._default_registry",
        type("_Reg", (), {"get": staticmethod(lambda _: _OneStubParser)})(),
    )

    from application_pipeline.__main__ import main

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1

    failures_dir = tmp_path / "application-pipeline" / ".runtime-data" / "failures"
    reports = list(failures_dir.glob("*.md")) if failures_dir.exists() else []
    assert len(reports) == 1, f"expected one failure report, got {reports}"

    body = reports[0].read_text(encoding="utf-8")
    assert "RuntimeError" in body


def test_default_llm_enricher_receives_run_scoped_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default LLM Enricher construction receives the active run-scoped Deduplication Store and Freshness Gate."""
    import application_pipeline.parser_log as parser_log

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    logs_dir = tmp_path / "logs"
    run_log = parser_log.RunLog(logs_dir)
    dedup_store = dedup_module.load(tmp_path / ".seen.json")
    captured: dict[str, object] = {}

    class _RunScopedDedupProxy:
        def __init__(self, base: object) -> None:
            self._base = base

        def __getattr__(self, name: str) -> object:
            return getattr(self._base, name)

    run_scope_dedup = _RunScopedDedupProxy(dedup_store)

    @contextmanager
    def _run_scope():
        yield run_scope_dedup

    dedup_store.run_scope = _run_scope  # type: ignore[method-assign]

    class _CapturingLLMEnricher:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            return _rejected_outcome(items)

    class _OneStubParser(_StubParserBase):
        def __enter__(self) -> "_OneStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://wire.example/1",
                    title="Python Engineer",
                    source="stub",
                )
            ]

    monkeypatch.setattr(
        "application_pipeline.orchestrator.LLMEnricher",
        _CapturingLLMEnricher,
    )

    run(
        config_path,
        extractor=_stub_extractor(),
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_store,
        run_log=run_log,
    )

    assert captured["run_log"] is run_log
    assert captured["failures_dir"] == tmp_path / ".runtime-data" / "failures"
    assert isinstance(captured["card_store"], CardStore)
    assert captured["dedup_store"] is run_scope_dedup
    assert isinstance(captured["freshness_gate"], FreshnessGate)


def test_injected_llm_enricher_bypasses_default_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected LLM Enricher instances bypass default construction cleanly."""
    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
    )
    card_store = _make_card_store(tmp_path)

    class _OneStubParser(_StubParserBase):
        def __enter__(self) -> "_OneStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://wire.example/2",
                    title="Python Engineer",
                    source="stub",
                )
            ]

    class _ShouldNotConstruct:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("default LLM Enricher must not be constructed")

    monkeypatch.setattr(
        "application_pipeline.orchestrator.LLMEnricher",
        _ShouldNotConstruct,
    )

    summary = run(
        config_path,
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _OneStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.classify_items == 1


# ---------------------------------------------------------------------------
# Issue #229 â€" status-row body refreshed on error exit paths
# ---------------------------------------------------------------------------


def test_classify_error_refreshes_status_body(tmp_path: Path) -> None:
    """ExtractorError from llm_enricher.enrich(): classify_relevance row body shows dropped count."""
    card_store = _make_card_store(tmp_path)

    class _ErrorEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            raise ExtractorError("classify boom")

    display = FakeStatusDisplay()

    run(
        _two_stub_config(tmp_path),
        llm_enricher=_ErrorEnricher(),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    classify_bodies = display.body_updates_for("llm classify relevance")
    assert classify_bodies, "expected at least one classify_relevance body update"
    last_body = classify_bodies[-1]
    assert "malformed" in last_body


def test_classify_error_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ExtractorError from llm_enricher.enrich() logs the same warning as before the refactor."""
    import logging

    card_store = _make_card_store(tmp_path)

    class _ErrorEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            raise ExtractorError("classify boom")

    with caplog.at_level(logging.WARNING, logger="application_pipeline.orchestrator"):
        run(
            _two_stub_config(tmp_path),
            llm_enricher=_ErrorEnricher(),
            extractor=_stub_extractor(),
            card_store=card_store,
            parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        )

    assert any(
        record.levelno == logging.WARNING
        and record.name == "application_pipeline.orchestrator"
        and record.getMessage() == "llm_enricher.enrich failed: classify boom"
        for record in caplog.records
    )


def test_clean_run_bodies_contain_no_error_tokens(tmp_path: Path) -> None:
    """On a clean run, classify and judge bodies contain no error tokens."""
    display = FakeStatusDisplay()

    run(
        _two_stub_config(tmp_path),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    for body in display.body_updates_for("llm classify relevance"):
        assert "calls_failed=" not in body
        assert "items_failed=" not in body


def test_judge_body_shows_finished_calls(tmp_path: Path) -> None:
    """judge_top_n success: a terminal print message is emitted with the card count."""
    card_store = _make_card_store(tmp_path)

    display = FakeStatusDisplay()

    run(
        _two_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    print_calls = [c for c in display.calls if c.method == "print"]
    assert any(
        "judge_top_n" in str(c.kwargs.get("message", "")) for c in print_calls
    ), "expected a judge_top_n terminal message"


# ---------------------------------------------------------------------------
# Issue #230 â€" live pending-depth signal
# ---------------------------------------------------------------------------


def test_pending_drains_to_zero_on_clean_run(tmp_path: Path) -> None:
    """Pending figures return to zero at end-of-run on a clean run."""
    display = FakeStatusDisplay()
    card_store = _make_card_store(tmp_path)

    run(
        _batch_size_config(tmp_path, 1),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _MixedLangParser199,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        status_display=display,
    )

    classify_bodies = display.body_updates_for("llm classify relevance")
    assert classify_bodies, "expected at least one classify body update"
    last_classify_body = classify_bodies[-1]
    assert "queued" not in last_classify_body, (
        f"Classify body still shows 'queued' at end-of-run (depth should be 0): {last_classify_body!r}"
    )


# ---------------------------------------------------------------------------
# Issue #233: classify batch failures logged to classify_relevance events
# ---------------------------------------------------------------------------


def test_clean_run_writes_classify_success_events(tmp_path: Path) -> None:
    """A clean run logs one classify_relevance success event per position."""
    import application_pipeline.parser_log as pl

    logs_dir = tmp_path / "logs"
    run_log = pl.RunLog(logs_dir)
    card_store = _make_card_store(tmp_path)

    run(
        _two_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    events_file = logs_dir / "llm" / "classify_relevance.events.jsonl"
    assert events_file.exists()
    rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    success_rows = [r for r in rows if r.get("event") == "classify_relevance"]
    assert len(success_rows) == 2


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
    """All judge_top_n verdicts are written to the daily results file in v2 format."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
            negative_keywords='["excluded"]',
        ),
        llm_enricher=_FakeLLMEnricherRejectJob1(card_store),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    results_dir = tmp_path / "results"
    dated_file = results_dir / f"{today}.md"
    assert dated_file.exists(), f"Expected daily file at {dated_file}"

    # _FakeLLMEnricherRejectJob1: 4 in-domain stubs (after 1 prefilter-drop and 1 classify-drop)
    # judge_top_n returns up to 5 verdicts; cards use v2 format: # **rank:** header
    content = dated_file.read_text(encoding="utf-8")
    cards = re.findall(r"^# \*\*\d+:\*\* .+", content, re.MULTILINE)
    assert len(cards) == 4
    assert summary.written == 4


def test_orchestrator_applies_verdicts_through_pool(
    tmp_path: Path, monkeypatch
) -> None:
    from datetime import datetime, timezone

    from application_pipeline.pool import Pool as RealPool

    today = datetime.now(timezone.utc).date().isoformat()
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)
    pool_apply_calls: list[list[tuple[int, int]]] = []

    class _PoolThatDropsVerdicts(RealPool):
        def apply_match_verdicts(
            self,
            verdicts: list[MatchVerdict],
            *,
            card_store,
            daily_results_file,
            dedup_store,
        ) -> int:
            pool_apply_calls.append(
                [(verdict.id, verdict.rank) for verdict in verdicts]
            )
            return 0

    monkeypatch.setattr(
        "application_pipeline.orchestrator.Pool", _PoolThatDropsVerdicts
    )

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
            negative_keywords='["excluded"]',
        ),
        llm_enricher=_FakeLLMEnricherRejectJob1(card_store),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert len(pool_apply_calls) == 1
    assert [rank for _, rank in pool_apply_calls[0]] == [1, 2, 3, 4]
    assert summary.written == 0
    assert not (tmp_path / "results" / f"{today}.md").exists()


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
    card_store = _make_card_store(tmp_path)

    run(
        config_path,
        llm_enricher=_FakeLLMEnricherRejectJob1(card_store),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
        run_log=run_log,
    )

    events_file = logs_dir / "pipeline" / "orchestrator.events.jsonl"
    assert events_file.exists()
    rows = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
    ]
    daily_written = [r for r in rows if r.get("event") == "daily_file_written"]
    assert len(daily_written) == 1
    assert daily_written[0]["card_count"] == 4


def test_run_reports_match_judge_completion_through_run_metrics(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)
    display = FakeStatusDisplay()
    logs_dir = tmp_path / "synched" / "logs"
    run_log = RunLog(logs_dir)

    summary = run(
        _two_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        status_display=display,
        run_log=run_log,
    )

    assert summary.written == 2
    assert "llm judge match" not in display.registered_names()

    judge_prints = [c for c in display.calls if c.method == "print"]
    assert any(
        c.name == "llm_judge_match"
        and str(c.kwargs["message"]) == "judge_top_n complete: wrote 2 cards"
        for c in judge_prints
    )

    run_log_text = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert "judges_sent=1" in run_log_text
    assert "judges_failed=0" in run_log_text
    assert "input_tokens=0" in run_log_text
    assert "output_tokens=0" in run_log_text


def test_run_reports_match_judge_failure_through_run_metrics(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "synched" / "logs"
    run_log = RunLog(logs_dir)
    display = FakeStatusDisplay()

    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    class _FailingJudge:
        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            raise ExtractorError("judge failed")

    summary = run(
        _two_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_FailingJudge(),
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value]
        dedup_store=dedup_module.load(seen_path),
        status_display=display,
        run_log=run_log,
    )

    assert summary.written == 0
    assert summary.errored == 1
    pipeline_updates = display.body_updates_for("pipeline")
    assert pipeline_updates[-1] == "discovered=2 written=0 errors=1"
    judge_phase_updates = [
        c
        for c in display.calls
        if c.method == "update_phase" and c.name == "llm judge match"
    ]
    assert judge_phase_updates == []

    run_log_text = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert "judges_sent=0" in run_log_text
    assert "judges_failed=1" in run_log_text


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
    card_store = _make_card_store(tmp_path)
    first_dedup_store = dedup_module.load(seen_path)

    first = run(
        config_path,
        llm_enricher=_FakeLLMEnricherRejectJob1(
            card_store, dedup_store=first_dedup_store
        ),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=first_dedup_store,
    )

    seen_after_first = json.loads(seen_path.read_text(encoding="utf-8"))

    second_dedup_store = dedup_module.load(seen_path)
    second = run(
        config_path,
        llm_enricher=_FakeLLMEnricherRejectJob1(
            card_store, dedup_store=second_dedup_store
        ),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=second_dedup_store,
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


def _make_advancing_quota_wall():  # type: ignore[return]
    """Return (slept_list, wall) where wall terminates after one sleep by advancing its clock."""
    from datetime import datetime, timedelta, timezone

    from application_pipeline.llm.quota import QuotaWall

    slept: list[float] = []
    slept_total = [0.0]
    base_now = [datetime.now(timezone.utc)]

    def _now() -> datetime:
        return base_now[0] + timedelta(seconds=slept_total[0])

    def _sleep(s: float) -> None:
        slept.append(s)
        slept_total[0] += s + 1.0  # jump past the wall in one step

    return slept, QuotaWall(now_fn=_now, sleep_fn=_sleep)


def test_quota_judge_retries_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ClaudeUsageLimitError on judge_top_n â†' orchestrator sleeps and retries; run completes."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    slept: list[float] = []
    monkeypatch.setattr("application_pipeline.orchestrator.time.sleep", slept.append)

    judge_call_count = [0]

    class _RetryJudge:
        def judge_top_n(
            self, candidates: "list[JudgeCandidate]"
        ) -> "tuple[list[MatchVerdict], CallUsage]":
            judge_call_count[0] += 1
            if judge_call_count[0] == 1:
                raise ClaudeUsageLimitError(
                    "quota", returncode=1, stdout="", stderr="quota", envelope=None
                )
            return [
                MatchVerdict(id=c.id, rank=i + 1) for i, c in enumerate(candidates[:5])
            ], _ZERO_USAGE

    summary = run(
        _two_stub_config(tmp_path),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_RetryJudge(),
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.written == 2
    assert len(slept) >= 1
    assert list((tmp_path / ".runtime-data" / "failures").glob("*.md")) == []


# ---------------------------------------------------------------------------
# Daily cutover â€" issue #390
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
    card_store = _make_card_store(tmp_path)

    run(
        config_path,
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _Daily390Parser,  # type: ignore[return-value, arg-type]
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
    """matched â†' expired transition on pool re-discovery removes the entry from extracts.json."""
    stale_url = "https://pool-reentry.example/stale-extract"
    (tmp_path / ".runtime-data").mkdir()
    seen_path = tmp_path / ".runtime-data" / "seen.json"
    extracts_path = tmp_path / ".runtime-data" / "extracts.json"

    seen_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": [stale_url],
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "matched",
                    "first_seen": "2024-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    # Write a v2-format extract for the URL (so _wipe_extracts_if_v1 keeps it)
    extracts_path.write_text(
        json.dumps(
            {
                "1": {
                    "header": "Extract Job · ACME · Hamburg",
                    "summary": "A stale extract.",
                }
            }
        ),
        encoding="utf-8",
    )

    _stale_date = _date.today() - _timedelta(days=500)

    class _StaleExtractParser(_StubParserBase):
        def __enter__(self) -> "_StaleExtractParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            # Include a stale posted_date so freshness gate can reject the stub in admit_stub()
            return [
                PositionStub(
                    url=stale_url,
                    title="Extract Job",
                    source="stub",
                    posted_date=_stale_date,
                )
            ]

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _StaleExtractParser,  # type: ignore[return-value, arg-type]
        # no dedup_store= so orchestrator wires extract_store into dedup_store
    )

    seen_after = json.loads(seen_path.read_text(encoding="utf-8"))
    assert (
        next(r["status"] for r in seen_after.values() if stale_url in r.get("urls", []))
        == "expired"
    )
    # After expiry, the extracts.json entry should be deleted by the freshness gate
    if extracts_path.exists():
        extracts_after = json.loads(extracts_path.read_text(encoding="utf-8"))
        assert "1" not in extracts_after


def test_discover_deadline_expiry_with_injected_stores_cleans_up_matched_extract(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".runtime-data" / "seen.json"
    extracts_path = tmp_path / ".runtime-data" / "extracts.json"
    seen_path.parent.mkdir(parents=True, exist_ok=True)

    canonical_url = "https://deadline.example/original"
    alias_url = "https://deadline.example/alias"
    seen_path.write_text(
        json.dumps(
            {
                "7": {
                    "urls": [canonical_url],
                    "company_lc": "acme",
                    "title_lc": "platform engineer",
                    "location_lc": "hamburg",
                    "status": "matched",
                    "status_last_changed": "2026-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    extracts_path.write_text(
        json.dumps(
            {
                "7": {
                    "header": "Platform Engineer\nAcme · Hamburg\n2026-01-01 · Senior",
                    "summary": "Persisted summary",
                    "body": "Persisted body",
                }
            }
        ),
        encoding="utf-8",
    )

    class _DeadlineExpiredParser(_StubParserBase):
        def __enter__(self) -> "_DeadlineExpiredParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=alias_url,
                    title="Platform Engineer",
                    source="stub",
                    company="Acme",
                    location="Hamburg",
                    deadline=_date.today(),
                )
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            raise AssertionError(
                "discover-arm freshness drop must stop before enrich()"
            )

    display = FakeStatusDisplay()
    card_store = load_card_store(extracts_path)
    dedup_store = dedup_module.load(seen_path)

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _DeadlineExpiredParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_store,
        card_store=card_store,
        status_display=display,
    )

    seen_after = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_after["7"]["status"] == "expired"
    assert seen_after["7"]["urls"] == [alias_url, canonical_url]
    assert card_store.get(7) is None

    transcript_rows = [
        json.loads(line)
        for line in (
            tmp_path
            / ".runtime-data"
            / "logs"
            / "pipeline"
            / "freshness.transcripts.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert any(
        row["url"] == alias_url
        and row["gate_arm"] == "discover"
        and row["passes"] is False
        and row["reason"] == "deadline_passed"
        for row in transcript_rows
    )

    gates_bodies = display.body_updates_for("parser bundesagentur api gates")
    assert any("1 freshness" in body for body in gates_bodies)

    classify_events = (
        tmp_path / ".runtime-data" / "logs" / "llm" / "classify_relevance.events.jsonl"
    )
    if classify_events.exists():
        rows = [
            json.loads(line)
            for line in classify_events.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert not any(row.get("url") == alias_url for row in rows)


def test_freshness_pool_reentry_fresh_position_stays_matched_and_reaches_judge(
    tmp_path: Path,
) -> None:
    """A still-fresh matched URL passes the gate, stays matched, and is judged via judge_top_n."""
    fresh_url = "https://pool-reentry.example/fresh"
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"

    seen_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": [fresh_url],
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "matched",
                    "first_seen": "2024-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    # Write v2-format extracts.json so _wipe_extracts_if_v1 keeps it
    extracts_path.write_text(
        json.dumps(
            {
                "1": {
                    "header": "Fresh Pool Job · ACME · Hamburg",
                    "summary": "A fresh pool job.",
                }
            }
        ),
        encoding="utf-8",
    )
    card_store = load_card_store(extracts_path)

    judge_candidate_ids: list[int] = []

    class _FreshPoolParser(_StubParserBase):
        def __enter__(self) -> "_FreshPoolParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=fresh_url, title="Fresh Pool Job", source="stub")]

    class _JudgeTrackingExtractor:
        def judge_top_n(
            self, candidates: "list[JudgeCandidate]"
        ) -> "tuple[list[MatchVerdict], CallUsage]":
            for c in candidates:
                judge_candidate_ids.append(c.id)
            return [
                MatchVerdict(id=c.id, rank=i + 1) for i, c in enumerate(candidates[:5])
            ], _ZERO_USAGE

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_JudgeTrackingExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _FreshPoolParser,  # type: ignore[return-value, arg-type]
    )

    seen_data_after = json.loads(seen_path.read_text(encoding="utf-8"))
    assert (
        next(
            r["status"]
            for r in seen_data_after.values()
            if fresh_url in r.get("urls", [])
        )
        != "expired"
    )
    assert 1 in judge_candidate_ids


# ---------------------------------------------------------------------------
# Parallel classify pool (issue #521 / ADR-0040)
# ---------------------------------------------------------------------------


def test_parallel_classify_pool_executes_concurrently(tmp_path: Path) -> None:
    """With claude_classify_parallelism=4 and 4 positions, at least 2 enrich calls run concurrently."""
    import threading
    import time as _time

    call_log: list[tuple[float, float]] = []
    log_lock = threading.Lock()
    card_store = _make_card_store(tmp_path)

    class _TimedEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            start = _time.monotonic()
            _time.sleep(0.05)
            end = _time.monotonic()
            with log_lock:
                call_log.append((start, end))
            card_store.put(
                listing_id,
                CardExtract(header=_FAKE_ENRICH_HEADER, summary=_FAKE_ENRICH_SUMMARY),
            )
            return _matched_outcome(items)

    class _FourStubParser(_StubParserBase):
        def __enter__(self) -> "_FourStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=f"https://par521.example/{i}", title=f"Job {i}", source="s"
                )
                for i in range(4)
            ]

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
            claude_classify_parallelism=4,
        ),
        llm_enricher=_TimedEnricher(),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _FourStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert len(call_log) == 4, f"Expected 4 enrich calls, got {len(call_log)}"
    overlapping = any(
        s2 < e1 and s1 < e2
        for i, (s1, e1) in enumerate(call_log)
        for j, (s2, e2) in enumerate(call_log)
        if i != j
    )
    assert overlapping, (
        "Expected at least 2 enrich calls to overlap in time â€” "
        f"call intervals: {call_log}"
    )


def test_parallel_classify_n1_recovers_serial_results(tmp_path: Path) -> None:
    """With claude_classify_parallelism=1, outcomes match a serial single-worker baseline."""
    seen_path = tmp_path / ".seen.json"
    results_dir = tmp_path / "results"
    card_store = _make_card_store(tmp_path)

    config_path = _write_config(
        tmp_path,
        sources='[SourceEntry(parser_type="bundesagentur_api")]',
        keywords='["python"]',
        locations='["Hamburg"]',
        include_remote=False,
        negative_keywords='["excluded"]',
        claude_classify_parallelism=1,
    )

    dedup_store = dedup_module.load(seen_path)
    summary = run(
        config_path,
        llm_enricher=_FakeLLMEnricherRejectJob1(card_store, dedup_store=dedup_store),
        extractor=_FakeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _LLMStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_store,
    )

    assert summary.discovered == 6
    assert summary.prefilter_dropped == 1
    assert summary.classifier_dropped == 1
    assert summary.written == 4

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    out_of_domain = [
        url
        for rec in seen_data.values()
        if rec["status"] == "out_of_domain"
        for url in rec.get("urls", [])
    ]
    assert len(out_of_domain) == 2

    content = _read_all_results(results_dir)
    # v2 card format: # **rank:** header
    cards = re.findall(r"^# \*\*\d+:\*\* .+", content, re.MULTILINE)
    assert len(cards) == 4


def test_parallel_classify_worker_exception_propagates(tmp_path: Path) -> None:
    """Uncaught exception in an enrich worker thread surfaces as run() exception."""
    card_store = _make_card_store(tmp_path)

    class _CrashingEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            raise RuntimeError("classify crash")

    class _OneStubParser2(_StubParserBase):
        def __enter__(self) -> "_OneStubParser2":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url="https://exc521.example/0", title="Job", source="s")
            ]

    with pytest.raises(RuntimeError, match="classify crash"):
        run(
            _write_config(
                tmp_path,
                sources='[SourceEntry(parser_type="bundesagentur_api")]',
                keywords='["python"]',
                locations='["Hamburg"]',
                include_remote=False,
                claude_classify_parallelism=2,
            ),
            llm_enricher=_CrashingEnricher(),
            extractor=_stub_extractor(),
            card_store=card_store,
            parser_registry=lambda _: _OneStubParser2,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        )


# ---------------------------------------------------------------------------
# pipeline: LLMEnricher + judge_top_n + renderer (issue #531)
# ---------------------------------------------------------------------------


class _DiscoverOnlyParser(_StubParserBase):
    """Parser that discovers stubs only; enrich() raises to confirm it is not called."""

    def __init__(self, stubs: "list[PositionStub] | None" = None, **_: object) -> None:
        self._stubs = stubs or [
            PositionStub(
                url="https://v2stub.example/0",
                title="ML Engineer",
                source="stub",
            )
        ]

    def __enter__(self) -> "_DiscoverOnlyParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> "list[PositionStub]":
        return self._stubs


_STUB_URL = "https://v2stub.example/0"
_CARD_HEADER = "ML Engineer · ACME GmbH · Hamburg"
_CARD_SUMMARY = "Exciting ML engineering role with competitive compensation."


class _FakeLLMEnricher:
    """Fake LLMEnricher: writes card to store and returns in-domain verdict."""

    def __init__(self, card_store: object, *, matches: bool = True) -> None:
        self._card_store = card_store
        self._matches = matches

    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
    ) -> AppliedClassifyOutcome:
        listing_id, stub, body = items[0]
        if self._matches:
            self._card_store.put(  # type: ignore[union-attr, attr-defined]
                listing_id,
                CardExtract(header=_CARD_HEADER, summary=_CARD_SUMMARY),
            )
            return _matched_outcome(items)
        return _rejected_outcome(items)


class _FakeJudgeExtractor:
    """Fake extractor with judge_top_n only."""

    def judge_top_n(
        self, candidates: "list[JudgeCandidate]"
    ) -> "tuple[list[MatchVerdict], CallUsage]":
        return [
            MatchVerdict(id=c.id, rank=i + 1) for i, c in enumerate(candidates[:5])
        ], _ZERO_USAGE


def test_pipeline_produces_cards(tmp_path: Path) -> None:
    """Cron path with LLMEnricher renders cards as # **{rank}:** {header}\\n\\n{summary}\\n."""
    card_store = load_card_store(tmp_path / "card_store.json")
    results_dir = tmp_path / "results"

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_FakeLLMEnricher(card_store),
        extractor=_FakeJudgeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _DiscoverOnlyParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.written == 1
    content = _read_all_results(results_dir)
    assert f"# **1:** {_CARD_HEADER}" in content
    assert _CARD_SUMMARY in content


# ---------------------------------------------------------------------------
# Gates bundle pre-enrich gating (issue #554)
# ---------------------------------------------------------------------------


def test_gates_bundle_dedup_hit_parser_enrich_not_called(tmp_path: Path) -> None:
    """A stub whose URL is already in the dedup store never reaches parser.enrich()."""
    enrich_calls: list[str] = []
    seen_url = "https://dedup-gate.example/job"

    class _TrackingParser(_StubParserBase):
        def __enter__(self) -> "_TrackingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=seen_url, title="Python Dev", source="stub")]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            enrich_calls.append(stub.url)
            return super().enrich(stub)

    seen_path = tmp_path / ".seen.json"
    seen_path.write_text(
        json.dumps(
            {
                "1": {
                    "urls": [seen_url],
                    "company_lc": None,
                    "title_lc": None,
                    "location_lc": None,
                    "status": "out_of_domain",
                    "first_seen": "2024-01-01",
                }
            }
        ),
        encoding="utf-8",
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
        parser_registry=lambda _: _TrackingParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert enrich_calls == [], "parser.enrich() must NOT be called for dedup-hit stubs"


def test_gates_bundle_blacklisted_title_parser_enrich_not_called(
    tmp_path: Path,
) -> None:
    """A stub whose title matches a NEGATIVE_KEYWORDS entry never reaches parser.enrich()."""
    enrich_calls: list[str] = []

    class _TrackingParser(_StubParserBase):
        def __enter__(self) -> "_TrackingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://bl-gate.example/job",
                    title="Senior Recruiter Position",
                    source="stub",
                )
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            enrich_calls.append(stub.url)
            return super().enrich(stub)

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
            negative_keywords='["recruiter"]',
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _TrackingParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert enrich_calls == [], (
        "parser.enrich() must NOT be called for blacklisted stubs"
    )


def test_gates_bundle_stale_stub_parser_enrich_not_called(tmp_path: Path) -> None:
    """A stub with posted_date older than MAX_LISTING_AGE_DAYS never reaches parser.enrich()."""
    enrich_calls: list[str] = []
    stale_date = _date(2020, 1, 1)

    class _TrackingParser(_StubParserBase):
        def __enter__(self) -> "_TrackingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://fresh-gate.example/job",
                    title="Python Dev",
                    source="stub",
                    posted_date=stale_date,
                )
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            enrich_calls.append(stub.url)
            return super().enrich(stub)

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _TrackingParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert enrich_calls == [], (
        "parser.enrich() must NOT be called for stubs with stale posted_date"
    )


def test_gates_bundle_passing_stub_parser_enrich_called(tmp_path: Path) -> None:
    """A stub passing all pre-enrich gates reaches parser.enrich() and LLM gets (stub, body)."""
    enrich_calls: list[str] = []
    llm_stub_urls: list[str] = []
    fresh_url = "https://pass-gate.example/job"
    card_store = _make_card_store(tmp_path)

    class _TrackingParser(_StubParserBase):
        def __enter__(self) -> "_TrackingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=fresh_url, title="Python Dev", source="stub")]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            enrich_calls.append(stub.url)
            return EnrichResult(
                stub=stub, body="job body text " + "x" * 87, mode="fallback"
            )

    class _TrackingLLMEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            llm_stub_urls.append(stub.url)
            return super().enrich(items)

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_TrackingLLMEnricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _TrackingParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert fresh_url in enrich_calls, "parser.enrich() must be called for passing stubs"
    assert fresh_url in llm_stub_urls, "LLM enricher must receive the same stub"


# ---------------------------------------------------------------------------
# Post-enrich Gates Bundle (issue #555)
# ---------------------------------------------------------------------------


def test_post_enrich_gates_drops_stub_with_expired_posted_date_backfilled(
    tmp_path: Path,
) -> None:
    """Stub with posted_date=None from discover, enrich() back-fills expired posted_date
    → dropped at post-enrich bundle, not passed to LLM Enricher, transcript has post_enrich row."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "logs"
    run_log = parser_log.RunLog(logs_dir)

    stale_url = "https://ba.example/stale"
    expired_date = _date(2020, 1, 1)
    llm_calls: list[str] = []

    class _BackfillParser(_StubParserBase):
        def __enter__(self) -> "_BackfillParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=stale_url, title="Stale Job", source="stub")]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            enriched = PositionStub(
                url=stub.url,
                title=stub.title,
                source=stub.source,
                posted_date=expired_date,
            )
            return EnrichResult(
                stub=enriched, body="job body " + "x" * 92, mode="native"
            )

    card_store = _make_card_store(tmp_path)

    class _TrackingLLMEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            llm_calls.append(stub.url)
            return super().enrich(items)

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_TrackingLLMEnricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _BackfillParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    assert stale_url not in llm_calls, (
        "expired back-filled stub must not reach LLM Enricher"
    )

    transcript_file = logs_dir / "pipeline" / "freshness.transcripts.jsonl"
    assert transcript_file.exists(), "freshness transcripts must be written"
    rows = [
        json.loads(line)
        for line in transcript_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    post_enrich_rows = [r for r in rows if r.get("gate_arm") == "post_enrich"]
    stale_row = next((r for r in post_enrich_rows if r.get("url") == stale_url), None)
    assert stale_row is not None, (
        "must have a post_enrich freshness row for the stale URL"
    )
    assert stale_row["passes"] is False


def test_post_enrich_expired_stub_absent_from_llm_classify_events(
    tmp_path: Path,
) -> None:
    """Stub dropped at post-enrich must produce no llm_classify_relevance event row."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "logs"
    run_log = parser_log.RunLog(logs_dir)

    stale_url = "https://ba.example/stale-no-llm"
    expired_date = _date(2020, 1, 1)

    class _BackfillParser(_StubParserBase):
        def __enter__(self) -> "_BackfillParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=stale_url, title="Stale Job", source="stub")]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            return EnrichResult(
                stub=PositionStub(
                    url=stub.url,
                    title=stub.title,
                    source=stub.source,
                    posted_date=expired_date,
                ),
                body="job body " + "x" * 92,
                mode="native",
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
        parser_registry=lambda _: _BackfillParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    classify_events_file = logs_dir / "llm" / "classify_relevance.events.jsonl"
    if classify_events_file.exists():
        events = [
            json.loads(line)
            for line in classify_events_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert not any(r.get("url") == stale_url for r in events), (
            "stale stub must not produce a classify_relevance event row"
        )


def test_post_enrich_non_expired_stub_reaches_llm_enricher(
    tmp_path: Path,
) -> None:
    """A stub whose posted_date is not expired after enrich() passes post-enrich bundle
    and reaches the LLM Enricher with the same (stub, body) shape."""
    fresh_url = "https://ba.example/fresh"
    recent_date = _date.today() - _timedelta(days=10)
    llm_calls: list[tuple[str, str]] = []

    class _FreshBackfillParser(_StubParserBase):
        def __enter__(self) -> "_FreshBackfillParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=fresh_url, title="Fresh Job", source="stub")]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            enriched = PositionStub(
                url=stub.url,
                title=stub.title,
                source=stub.source,
                posted_date=recent_date,
            )
            return EnrichResult(
                stub=enriched, body="fresh job body " + "x" * 86, mode="native"
            )

    card_store = _make_card_store(tmp_path)

    class _TrackingLLMEnricher(_FakeLLMEnricherHelper):
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            llm_calls.append((stub.url, body))
            return super().enrich(items)

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_TrackingLLMEnricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _FreshBackfillParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert len(llm_calls) == 1, "fresh stub must reach LLM Enricher exactly once"
    url_received, body_received = llm_calls[0]
    assert url_received == fresh_url
    assert body_received == "fresh job body " + "x" * 86


def test_post_llm_freshness_drop_still_works_after_post_enrich_bundle(
    tmp_path: Path,
) -> None:
    """Existing post-LLM Freshness behavior is preserved: LLM-inferred expired
    posted_date still drops the candidate with gate_arm: 'post_llm'."""
    import application_pipeline.parser_log as parser_log

    logs_dir = tmp_path / "logs"
    run_log = parser_log.RunLog(logs_dir)

    url = "https://ba.example/post-llm-drop"
    # The LLM classifier will return a header with an expired posted_date in line 3.
    stale_header = "ML Engineer\nCorp · Berlin · hybrid\n2020-01-01 · mid · —"

    class _SimpleParser(_StubParserBase):
        def __enter__(self) -> "_SimpleParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=url, title="ML Engineer", source="stub")]

    extractor_mock = MagicMock()
    extractor_mock.classify_relevance.return_value = (
        [RelevanceVerdict(matches=True, header=stale_header, summary="Old role.")],
        _ZERO_USAGE,
    )
    extractor_mock.judge_top_n.return_value = ([], _ZERO_USAGE)

    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=extractor_mock,
        card_store=card_store,
        parser_registry=lambda _: _SimpleParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
        run_log=run_log,
    )

    transcript_file = logs_dir / "pipeline" / "freshness.transcripts.jsonl"
    assert transcript_file.exists(), "freshness transcripts must be written"
    rows = [
        json.loads(line)
        for line in transcript_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    post_llm_rows = [
        r for r in rows if r.get("gate_arm") == "post_llm" and r.get("url") == url
    ]
    assert len(post_llm_rows) == 1, "must have a post_llm freshness row"
    assert post_llm_rows[0]["passes"] is False


def test_post_llm_stale_outcome_is_dropped_and_fresh_batch_peer_reaches_judge(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)
    stale_url = "https://ba.example/post-llm-stale"
    fresh_url = "https://ba.example/post-llm-fresh"
    stale_header = "ML Engineer\nCorp · Berlin · hybrid\n2020-01-01 · mid · —"
    fresh_header = "Data Engineer\nCorp · Hamburg · remote\n2026-01-10 · senior · —"
    judge_candidate_ids: list[list[int]] = []

    class _TwoStubParser(_StubParserBase):
        def __enter__(self) -> "_TwoStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=stale_url, title="ML Engineer", source="stub"),
                PositionStub(url=fresh_url, title="Data Engineer", source="stub"),
            ]

    class _TrackingExtractor:
        def classify_relevance(
            self, items: list[ClassifyItem]
        ) -> tuple[list[RelevanceVerdict | None], CallUsage]:
            assert len(items) == 2
            return (
                [
                    RelevanceVerdict(
                        matches=True,
                        header=stale_header,
                        summary="Old role.",
                    ),
                    RelevanceVerdict(
                        matches=True,
                        header=fresh_header,
                        summary="Fresh role.",
                    ),
                ],
                _ZERO_USAGE,
            )

        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            judge_candidate_ids.append([c.id for c in candidates])
            return (
                [
                    MatchVerdict(id=c.id, rank=i + 1)
                    for i, c in enumerate(candidates[:5])
                ],
                _ZERO_USAGE,
            )

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
            claude_classify_batch_size=2,
        ),
        extractor=_TrackingExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _TwoStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
        run_log=run_log,
    )

    assert summary.classifier_dropped == 1
    assert summary.written == 1
    assert card_store.get(1) is None
    assert card_store.get(2) is not None
    assert judge_candidate_ids == [[2]]

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert (
        next(r["status"] for r in seen_data.values() if stale_url in r.get("urls", []))
        == "expired"
    )
    assert (
        next(r["status"] for r in seen_data.values() if fresh_url in r.get("urls", []))
        == "selected_by_judge"
    )

    transcript_rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "freshness.transcripts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert [
        row
        for row in transcript_rows
        if row["url"] == stale_url and row["gate_arm"] == "post_llm"
    ] == [
        {
            "url": stale_url,
            "title": "ML Engineer",
            "source": "stub",
            "posted_date": "2020-01-01",
            "deadline": None,
            "anchored_today": transcript_rows[0]["anchored_today"],
            "age_days": transcript_rows[0]["age_days"],
            "passes": False,
            "reason": "too_old",
            "gate_arm": "post_llm",
        }
    ]


def test_parser_enrich_skip_outcomes_keep_log_artifacts(tmp_path: Path) -> None:
    """Parser Intake skip outcomes keep the same Log Artifacts after parser-thread delegation."""
    import httpx

    logs_dir = tmp_path / "synched" / "logs"
    run_log = RunLog(logs_dir)
    urls = [
        "https://skip-log.example/enrich-failed",
        "https://skip-log.example/body-oversized",
        "https://skip-log.example/transient-http",
    ]

    class _SkipOutcomeParser(_StubParserBase):
        def __enter__(self) -> "_SkipOutcomeParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=url, title=f"Job {index}", source="stub")
                for index, url in enumerate(urls)
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            if stub.url == urls[0]:
                raise EnrichFailedError("native fetch failed")
            if stub.url == urls[1]:
                raise OversizedBodyError(
                    url=stub.url, source=stub.source, body_len=4321
                )
            raise httpx.HTTPStatusError(
                "503 Service Unavailable",
                request=httpx.Request("GET", stub.url),
                response=httpx.Response(503),
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
        parser_registry=lambda _: _SkipOutcomeParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
        run_log=run_log,
    )

    assert summary.enrich_failed == 1
    assert summary.parsers_dead == 0

    pipeline_rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "orchestrator.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        row.get("event") == "enrich_failed"
        and row.get("url") == urls[0]
        and row.get("source") == "stub"
        for row in pipeline_rows
    ), "parser enrich failure must keep the existing enrich_failed Log Artifact"

    llm_rows = [
        json.loads(line)
        for line in (logs_dir / "llm" / "enricher.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        row.get("event") == "body_oversized"
        and row.get("url") == urls[1]
        and row.get("source") == "stub"
        and row.get("body_len") == 4321
        for row in llm_rows
    ), "oversized-body skip must keep the existing body_oversized Log Artifact"
    assert any(
        row.get("event") == "fetch_transient_error"
        and row.get("url") == urls[2]
        and row.get("source") == "stub"
        and "503 Service Unavailable" in str(row.get("error"))
        for row in llm_rows
    ), "transient HTTP skip must keep the existing fetch_transient_error Log Artifact"


# ---------------------------------------------------------------------------
# Issue #586: Enrich inline on parser thread
# ---------------------------------------------------------------------------


def test_body_fetch_runs_on_parser_thread_not_classify_worker(tmp_path: Path) -> None:
    """parser.enrich() must be called on the parser thread, not an enrich/classify worker thread."""
    import threading as _threading

    enrich_thread_names: list[str] = []

    class _TrackingParser(_StubParserBase):
        def __enter__(self) -> "_TrackingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://track.example/0", title="Job 0", source="stub"
                )
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            enrich_thread_names.append(_threading.current_thread().name)
            return EnrichResult(stub=stub, body="body text " + "x" * 91, mode="native")

    card_store = _make_card_store(tmp_path)
    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _TrackingParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert len(enrich_thread_names) == 1, (
        f"enrich() must be called once, got {enrich_thread_names}"
    )
    assert enrich_thread_names[0].startswith("parser-"), (
        f"enrich() must run on parser thread, got thread name: {enrich_thread_names[0]!r}"
    )


def test_enrich_failed_error_on_parser_enrich_increments_counter_run_continues(
    tmp_path: Path,
) -> None:
    """EnrichFailedError from parser.enrich() increments enrich_failed; run continues for other stubs."""
    seen_path = tmp_path / ".seen.json"
    card_store = _make_card_store(tmp_path)
    _URLS = [f"https://ef-test.example/{i}" for i in range(3)]

    class _EnrichFailParser(_StubParserBase):
        def __enter__(self) -> "_EnrichFailParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(url=_URLS[i], title=f"Job {i}", source="stub")
                for i in range(3)
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            if stub.url == _URLS[1]:
                raise EnrichFailedError("native fetch failed")
            return EnrichResult(stub=stub, body="body " + "x" * 96, mode="native")

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_make_fake_llm_enricher(card_store),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _EnrichFailParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(seen_path),
    )

    assert summary.enrich_failed == 1
    assert summary.parsers_dead == 0, (
        "EnrichFailedError must not kill the parser thread"
    )
    assert summary.written == 2, "other two stubs must still be processed"

    seen_data = (
        json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    )
    assert not any(_URLS[1] in r.get("urls", []) for r in seen_data.values()), (
        "EnrichFailedError must not write to seen.json (#572)"
    )


def test_classify_workers_sized_by_claude_classify_parallelism(tmp_path: Path) -> None:
    """classify workers are named 'classify-worker-N', and enrich-worker-N no longer exists."""
    import threading as _threading

    classify_worker_names: list[str] = []
    enrich_worker_names: list[str] = []

    card_store = _make_card_store(tmp_path)

    class _CapturingEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            name = _threading.current_thread().name
            if name.startswith("classify-worker-"):
                classify_worker_names.append(name)
            elif name.startswith("enrich-worker-"):
                enrich_worker_names.append(name)
            card_store.put(
                listing_id,
                CardExtract(header="H", summary="S"),
            )
            return _matched_outcome(items)

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
            claude_classify_parallelism=2,
        ),
        llm_enricher=_CapturingEnricher(),
        extractor=_stub_extractor(),
        card_store=card_store,
        parser_registry=lambda _: _StubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert enrich_worker_names == [], (
        "no enrich-worker threads must exist in the new architecture"
    )
    assert all(name.startswith("classify-worker-") for name in classify_worker_names), (
        f"LLM classify must run on classify-worker threads, got: {classify_worker_names}"
    )


def test_parser_dead_failure_reports_distinguish_exception_types(
    tmp_path: Path,
) -> None:
    """Auth expiry (401), upstream error (503), and redirect (3xx) produce distinguishable reports."""
    from application_pipeline.http import HttpParserFatalError, HttpRedirectResponse

    error_cases = [
        HttpParserFatalError("auth: https://api.example.com/jobs status=401"),
        HttpParserFatalError("upstream: https://api.example.com/jobs status=503"),
        HttpRedirectResponse(301, "https://new.example.com/jobs"),
    ]

    reports: list[str] = []
    for i, exc in enumerate(error_cases):
        exc_ref = exc

        class _DyingParser(_StubParserBase):
            def __enter__(self) -> "_DyingParser":
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def discover(self, query: ParserQuery):  # type: ignore[return]
                raise exc_ref  # noqa: B023
                yield  # pragma: no cover

        sub = tmp_path / str(i)
        sub.mkdir()
        card_store_i = _make_card_store(tmp_path, f"card_store_{i}.json")
        run(
            _write_config(
                sub,
                sources='[SourceEntry(parser_type="bundesagentur_api")]',
                keywords='["python"]',
                locations='["Hamburg"]',
                include_remote=False,
            ),
            llm_enricher=_make_fake_llm_enricher(card_store_i),
            extractor=_stub_extractor(),
            card_store=card_store_i,
            parser_registry=lambda _: _DyingParser,  # type: ignore[return-value, arg-type]
            dedup_store=dedup_module.load(sub / ".seen.json"),
        )
        report_dir = sub / ".runtime-data" / "failures"
        reports.append(next(report_dir.glob("*.md")).read_text(encoding="utf-8"))

    auth_report, upstream_report, redirect_report = reports

    assert "status=401" in auth_report, "auth report must mention HTTP 401"
    assert "status=503" in upstream_report, "upstream report must mention HTTP 503"
    assert "301" in redirect_report or "redirect" in redirect_report.lower(), (
        "redirect report must mention redirect or status 301"
    )
    assert "HttpParserFatalError" in auth_report
    assert "HttpParserFatalError" in upstream_report
    assert "HttpRedirectResponse" in redirect_report
    assert auth_report != upstream_report, "auth and upstream reports must differ"
    assert auth_report != redirect_report, "auth and redirect reports must differ"


# ---------------------------------------------------------------------------
# Post-enrich dedup check (issue #613, issue #718)
# ---------------------------------------------------------------------------


def test_classify_forwarded_queue_delivery_keeps_enriched_stub_listing_id_and_body(
    tmp_path: Path,
) -> None:
    """A classify-forwarded Parser Intake outcome reaches the LLM Enricher unchanged."""
    captured_items: list[tuple[int, PositionStub, str]] = []

    class _ForwardingParser(_StubParserBase):
        def __enter__(self) -> "_ForwardingParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url="https://queue-forward.example/new",
                    title="Backend Engineer",
                    source="stub",
                    posted_date=date(2026, 5, 29),
                )
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            enriched = PositionStub(
                url=stub.url,
                title=stub.title,
                source=stub.source,
                company="Acme",
                location="Hamburg",
                posted_date=stub.posted_date,
            )
            return EnrichResult(
                stub=enriched,
                body="Fresh backend role " + "x" * 120,
                mode="native",
            )

    class _CapturingLLMEnricher:
        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            captured_items.extend(items)
            return _matched_outcome(items)

    summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["backend"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_CapturingLLMEnricher(),
        extractor=_stub_extractor(),
        parser_registry=lambda _: _ForwardingParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    assert summary.discovered == 1
    assert summary.classify_items == 1
    assert captured_items == [
        (
            1,
            PositionStub(
                url="https://queue-forward.example/new",
                title="Backend Engineer",
                source="stub",
                company="Acme",
                location="Hamburg",
                posted_date=date(2026, 5, 29),
            ),
            "Fresh backend role " + "x" * 120,
        )
    ]


# ---------------------------------------------------------------------------
# Renderer integration: URL and body appear in Daily Results File (#673)
# ---------------------------------------------------------------------------


def test_rendered_card_includes_url_and_body(tmp_path: Path) -> None:
    """Daily Results File card includes listing URL and raw body after the judge renders it."""
    card_store = load_card_store(tmp_path / "card_store.json")
    results_dir = tmp_path / "results"
    stub_url = "https://render-test.example/job/42"
    stub_body_prefix = "render test body"

    class _SingleStubParser(_StubParserBase):
        def __enter__(self) -> "_SingleStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [PositionStub(url=stub_url, title="Render Job", source="stub")]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            return EnrichResult(
                stub=stub, body=stub_body_prefix + " x" * 50, mode="fallback"
            )

    class _BodyCapturingEnricher:
        def __init__(self, cs: CardStore) -> None:
            self._cs = cs

        def enrich(
            self, items: list[tuple[int, PositionStub, str]]
        ) -> AppliedClassifyOutcome:
            listing_id, stub, body = items[0]
            self._cs.put(
                listing_id,
                CardExtract(header=_CARD_HEADER, summary=_CARD_SUMMARY, body=body),
            )
            return _matched_outcome(items)

    run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        llm_enricher=_BodyCapturingEnricher(card_store),
        extractor=_FakeJudgeExtractor(),
        card_store=card_store,
        parser_registry=lambda _: _SingleStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_module.load(tmp_path / ".seen.json"),
    )

    content = _read_all_results(results_dir)
    assert stub_url in content, "listing URL must appear in the rendered card"
    assert stub_body_prefix in content, "raw body must appear in the rendered card"


def test_matched_llm_enricher_outcome_reaches_judge_and_daily_results_file(
    tmp_path: Path,
) -> None:
    """A matched LLM Enricher outcome persists Card data and reaches judge/rendering."""
    runtime_dir = tmp_path / ".runtime-data"
    extracts_path = runtime_dir / "extracts.json"
    seen_path = runtime_dir / "seen.json"
    results_dir = tmp_path / "results"
    stub_url = "https://judge-flow.example/job/1"
    body = "Persisted body from the real LLM Enricher path. " + "x" * 120
    header = "Senior Python Engineer\nAcme · Hamburg · remote\n2026-01-01 · Senior"
    card_summary = "Strong fit for the candidate profile."
    judge_candidates: list[JudgeCandidate] = []

    class _SingleStubParser(_StubParserBase):
        def __enter__(self) -> "_SingleStubParser":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> list[PositionStub]:
            return [
                PositionStub(
                    url=stub_url,
                    title="Senior Python Engineer",
                    source="stub",
                    company="Acme",
                    location="Hamburg",
                )
            ]

        def enrich(self, stub: PositionStub) -> EnrichResult:
            return EnrichResult(stub=stub, body=body, mode="fallback")

    class _Extractor:
        def classify_relevance(
            self, items: list[object]
        ) -> tuple[list[RelevanceVerdict | None], CallUsage]:
            return [
                RelevanceVerdict(matches=True, header=header, summary=card_summary)
            ], _ZERO_USAGE

        def judge_top_n(
            self, candidates: list[JudgeCandidate]
        ) -> tuple[list[MatchVerdict], CallUsage]:
            judge_candidates.extend(candidates)
            return [MatchVerdict(id=candidates[0].id, rank=1)], _ZERO_USAGE

    extractor = _Extractor()
    dedup_store = dedup_module.load(seen_path)
    card_store = load_card_store(extracts_path)

    run_summary = run(
        _write_config(
            tmp_path,
            sources='[SourceEntry(parser_type="bundesagentur_api")]',
            keywords='["python"]',
            locations='["Hamburg"]',
            include_remote=False,
        ),
        extractor=extractor,
        card_store=card_store,
        parser_registry=lambda _: _SingleStubParser,  # type: ignore[return-value, arg-type]
        dedup_store=dedup_store,
    )

    assert run_summary.written == 1

    reloaded_card_store = load_card_store(extracts_path)
    persisted = reloaded_card_store.get(1)
    assert persisted is not None
    assert persisted.header == header
    assert persisted.summary == card_summary
    assert persisted.body == body

    assert judge_candidates == [
        JudgeCandidate(id=1, header=header, summary=card_summary)
    ]

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert next(iter(seen_data.values()))["status"] == "selected_by_judge"

    content = _read_all_results(results_dir)
    assert "# **1:** Senior Python Engineer" in content
    assert "Acme · Hamburg · remote" in content
    assert "2026-01-01 · Senior" in content
    assert card_summary in content
    assert body in content
