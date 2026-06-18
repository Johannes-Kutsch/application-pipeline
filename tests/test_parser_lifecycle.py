from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from fake_status_display import FakeStatusDisplay

from application_pipeline.classify_stage import ClassifyStage, ClassifyStageHandoff
from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import load as load_dedup
from application_pipeline.dedup_counters import DedupCounters
from application_pipeline.failure_report import FailureReportWriter
from application_pipeline.extracts.card_store import load_card_store
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.llm import quota as _quota
from application_pipeline.llm.types import (
    AppliedClassifyOutcome,
    AppliedClassifyItemOutcome,
    MatchedListing,
)
from application_pipeline.parser_lifecycle import (
    ParserLifecycleCollaborators,
    ParserLifecycleExecution,
    ParserLifecyclePlan,
    run_parser_lifecycle,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import Parser, ParserQuery
from application_pipeline.parsers.types import City, EnrichResult, PositionStub, Remote
from application_pipeline.pool import Pool
from application_pipeline.prefilter_gate import PreFilterGate
from application_pipeline.run_metrics import RunMetrics


class _RunState:
    @property
    def is_aborted(self) -> bool:
        return False


class _ClassifyStageRunState:
    @property
    def is_degraded(self) -> bool:
        return False

    def set_aborted(self, exc: BaseException) -> None:
        raise AssertionError(f"classify stage should not abort: {exc}")


class _NoopHandoff:
    def submit_ready(
        self,
        *,
        listing_id: int,
        stub: PositionStub,
        raw_description: str,
        parser_id: str,
    ) -> None:
        del listing_id, stub, raw_description, parser_id


class _NoopClassifyMetrics:
    def observe_classify_submission(self, observation: object) -> None:
        del observation

    def observe_classify_batch_start(self, observation: object) -> None:
        del observation

    def observe_classify_batch_outcome(self, observation: object) -> None:
        del observation

    def observe_classify_batch_failure(self, observation: object) -> None:
        del observation

    def observe_classify_retryable(self, observation: object) -> None:
        del observation

    def observe_classify_stage_completion(self, observation: object) -> None:
        del observation


class _ExplosiveTruthinessHandoff:
    def __bool__(self) -> bool:
        raise AssertionError("parser-dead handling must not evaluate classify handoff")

    def submit_ready(
        self,
        *,
        listing_id: int,
        stub: PositionStub,
        raw_description: str,
        parser_id: str,
    ) -> None:
        raise AssertionError("dead parser must not submit classify work")


class _SlowParserStartedRunLog(RunLog):
    def event(self, component_id: str, event_type: str, **fields: object) -> None:
        if event_type == "parser started":
            time.sleep(0.05)
        super().event(component_id, event_type, **fields)


class _ImmediateParser:
    def __enter__(self) -> _ImmediateParser:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        raise AssertionError("empty discover should not call enrich()")


class _ForwardingParser:
    def __init__(self, *, error_after_first_yield: bool = False) -> None:
        self.discovery_calls: list[ParserQuery] = []
        self._error_after_first_yield = error_after_first_yield

    def __enter__(self) -> _ForwardingParser:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery):
        self.discovery_calls.append(query)
        yield PositionStub(
            url=f"https://example.com/{query.keyword}",
            title=f"{query.keyword.title()} Engineer",
            source="test",
        )
        if self._error_after_first_yield:
            raise RuntimeError("boom mid-discover")

    def enrich(self, stub: PositionStub) -> EnrichResult:
        return EnrichResult(stub=stub, body="x" * 200, mode="fallback")


class _SleepingParser:
    def __init__(self, *, sleep_s: float) -> None:
        self._sleep_s = sleep_s

    def __enter__(self) -> _SleepingParser:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> list[PositionStub]:
        time.sleep(self._sleep_s)
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        raise AssertionError("empty discover should not call enrich()")


class _TwoSilencesAroundProgressParser:
    def __init__(self, *, sleep_s: float) -> None:
        self._sleep_s = sleep_s

    def __enter__(self) -> _TwoSilencesAroundProgressParser:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery):
        time.sleep(self._sleep_s)
        yield PositionStub(
            url=f"https://example.com/{query.keyword}-1",
            title=f"{query.keyword.title()} Engineer I",
            source="test",
        )
        time.sleep(self._sleep_s)
        yield PositionStub(
            url=f"https://example.com/{query.keyword}-2",
            title=f"{query.keyword.title()} Engineer II",
            source="test",
        )

    def enrich(self, stub: PositionStub) -> EnrichResult:
        return EnrichResult(stub=stub, body="x" * 200, mode="fallback")


class _LifecycleAccountingParser:
    def __enter__(self) -> _LifecycleAccountingParser:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery):
        if query.keyword == "python":
            from application_pipeline.parsers import NotServedQuery

            yield NotServedQuery()
            return
        yield PositionStub(
            url=f"https://example.com/{query.keyword}",
            title=f"{query.keyword.title()} Engineer",
            source="test",
        )

    def enrich(self, stub: PositionStub) -> EnrichResult:
        return EnrichResult(stub=stub, body="x" * 200, mode="fallback")


class _PrefilterDropParser:
    def __enter__(self) -> _PrefilterDropParser:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery):
        yield PositionStub(
            url="https://example.com/blacklisted",
            title="Senior Python Developer",
            source="test",
            company="Acme",
            location="Hamburg",
        )

    def enrich(self, stub: PositionStub) -> EnrichResult:
        raise AssertionError("prefilter drop should stop before enrich()")


class _DiscoverCrashParser:
    def __enter__(self) -> _DiscoverCrashParser:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery):  # type: ignore[return]
        raise RuntimeError("fatal error in parser")
        yield  # pragma: no cover

    def enrich(self, stub: PositionStub) -> EnrichResult:
        raise AssertionError("discover crash should not call enrich()")


class _MatchedEnricher:
    def enrich(
        self, items: list[tuple[int, PositionStub, str]]
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


def _make_plan(
    tmp_path: Path,
    *,
    parser: Parser,
    classify_handoff: ClassifyStageHandoff | None = None,
    run_log: RunLog | None = None,
    keywords: list[str] | None = None,
    locations: list[City | Remote] | None = None,
    stall_threshold_s: float = 60.0,
) -> ParserLifecyclePlan:
    logs_dir = tmp_path / "logs"
    configured_run_log = run_log or RunLog(logs_dir)
    card_store = load_card_store(tmp_path / "extracts.json")
    dedup = load_dedup(
        tmp_path / ".seen.json",
        card_store=card_store,
        run_log=configured_run_log,
    )
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=configured_run_log)
    configured_keywords = keywords or ["python"]
    configured_locations = locations or [City("Hamburg")]
    metrics.register_parser(
        "test_parser",
        order=0,
        total_queries=len(configured_keywords) * len(configured_locations),
        has_native_enrich=False,
    )
    collaborators = ParserLifecycleCollaborators(
        run_log=configured_run_log,
        run_state=_RunState(),
        freshness=FreshnessGate(
            anchored_today=date(2026, 6, 16),
            max_listing_age_days=30,
            dedup=dedup,
            run_log=configured_run_log,
            card_store=card_store,
        ),
        prefilter=PreFilterGate(
            blacklist=[],
            dedup=dedup,
            run_log=configured_run_log,
        ),
        content_gate=ContentGate(run_log=configured_run_log),
        dedup=dedup,
        dedup_counters=DedupCounters(display=display, run_log=configured_run_log),
        pool=Pool(),
        metrics=metrics,
        card_store=card_store,
        failure_report_writer=FailureReportWriter(tmp_path / "failures"),
        stall_threshold_s=stall_threshold_s,
    )
    return ParserLifecyclePlan(
        parsers=[
            ParserLifecycleExecution(
                parser=parser,
                parser_id="test_parser",
                classify_handoff=(
                    _NoopHandoff() if classify_handoff is None else classify_handoff
                ),
            )
        ],
        keywords=configured_keywords,
        locations=configured_locations,
        collaborators=collaborators,
    )


def _make_plan_with_display(
    tmp_path: Path,
    *,
    parser: Parser,
    keywords: list[str] | None = None,
    locations: list[City | Remote] | None = None,
    blacklist: list[str] | None = None,
) -> tuple[ParserLifecyclePlan, FakeStatusDisplay]:
    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    card_store = load_card_store(tmp_path / "extracts.json")
    dedup = load_dedup(
        tmp_path / ".seen.json",
        card_store=card_store,
        run_log=run_log,
    )
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    configured_keywords = keywords or ["python"]
    configured_locations = locations or [City("Hamburg")]
    metrics.register_parser(
        "test_parser",
        order=0,
        total_queries=len(configured_keywords) * len(configured_locations),
        has_native_enrich=False,
    )
    plan = ParserLifecyclePlan(
        parsers=[
            ParserLifecycleExecution(
                parser=parser,
                parser_id="test_parser",
                classify_handoff=_NoopHandoff(),
            )
        ],
        keywords=configured_keywords,
        locations=configured_locations,
        collaborators=ParserLifecycleCollaborators(
            run_log=run_log,
            run_state=_RunState(),
            freshness=FreshnessGate(
                anchored_today=date(2026, 6, 16),
                max_listing_age_days=30,
                dedup=dedup,
                run_log=run_log,
                card_store=card_store,
            ),
            prefilter=PreFilterGate(
                blacklist=[] if blacklist is None else blacklist,
                dedup=dedup,
                run_log=run_log,
            ),
            content_gate=ContentGate(run_log=run_log),
            dedup=dedup,
            dedup_counters=DedupCounters(display=display, run_log=run_log),
            pool=Pool(),
            metrics=metrics,
            card_store=card_store,
            failure_report_writer=FailureReportWriter(tmp_path / "failures"),
        ),
    )
    return plan, display


def test_parser_started_is_logged_before_query_worklist_begins(tmp_path: Path) -> None:
    run_log = _SlowParserStartedRunLog(tmp_path / "logs")
    plan = _make_plan(
        tmp_path,
        parser=_ImmediateParser(),
        run_log=run_log,
    )

    run_parser_lifecycle(plan)

    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "parser" / "test_parser.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert [row["event"] for row in rows[:2]] == [
        "parser started",
        "query_started",
    ]


def test_query_heartbeats_wrap_each_query_with_keyword_and_location(
    tmp_path: Path,
) -> None:
    parser = _ForwardingParser()
    plan = _make_plan(
        tmp_path,
        parser=parser,
        keywords=["python", "django"],
        locations=[City("Hamburg"), Remote()],
    )

    run_parser_lifecycle(plan)

    rows = [
        {k: v for k, v in json.loads(line).items() if k != "ts"}
        for line in (tmp_path / "logs" / "parser" / "test_parser.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert rows == [
        {"event": "parser started"},
        {"event": "query_started", "keyword": "python", "location": "Hamburg"},
        {"event": "query_ended", "keyword": "python", "location": "Hamburg"},
        {"event": "query_started", "keyword": "python", "location": "Remote"},
        {"event": "query_ended", "keyword": "python", "location": "Remote"},
        {"event": "query_started", "keyword": "django", "location": "Hamburg"},
        {"event": "query_ended", "keyword": "django", "location": "Hamburg"},
        {"event": "query_started", "keyword": "django", "location": "Remote"},
        {"event": "query_ended", "keyword": "django", "location": "Remote"},
    ]
    assert [
        (query.keyword, type(query.location).__name__)
        for query in parser.discovery_calls
    ] == [
        ("python", "City"),
        ("python", "Remote"),
        ("django", "City"),
        ("django", "Remote"),
    ]


def test_accepted_listing_reaches_classify_stage_through_parser_lifecycle(
    tmp_path: Path,
) -> None:
    parser = _ForwardingParser()
    classify_pool = Pool()
    classify_metrics = _NoopClassifyMetrics()
    stage = ClassifyStage(
        batch_size=1,
        parallelism=1,
        pool_collector=classify_pool,
        llm_enricher=_MatchedEnricher(),
        metrics=classify_metrics,
        run_state=_ClassifyStageRunState(),
        run_log=RunLog(tmp_path / "logs"),
        quota_wall=_quota.QuotaWall(),
    )
    plan = _make_plan(
        tmp_path,
        parser=parser,
        classify_handoff=stage.handoff_for(
            parser_id="test_parser",
            metrics=classify_metrics,
        ),
    )

    stage.start()
    run_parser_lifecycle(plan)
    stage.close()
    completion = stage.wait()

    assert completion.first_failure is None
    assert classify_pool.pool_size == 1


def test_prefilter_skip_through_parser_lifecycle_preserves_log_rows_and_metrics(
    tmp_path: Path,
) -> None:
    plan, display = _make_plan_with_display(
        tmp_path,
        parser=_PrefilterDropParser(),
        blacklist=["python"],
    )

    run_parser_lifecycle(plan)

    transcript_rows = [
        {k: v for k, v in json.loads(line).items() if k != "ts"}
        for line in (tmp_path / "logs" / "pipeline" / "prefilter.transcripts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert transcript_rows == [
        {
            "url": "https://example.com/blacklisted",
            "title": "Senior Python Developer",
            "source": "test",
            "passes": False,
            "reason": "blacklist_drop",
            "blacklist_matches": [{"term": "python"}],
            "title_len": len("Senior Python Developer"),
        }
    ]
    assert any(
        call.method in ("register", "update_body")
        and call.name == "parser test parser gates"
        and call.kwargs["body"] == "1 pre-filter"
        for call in display.calls
    )


def test_query_ended_is_still_logged_when_discover_raises(tmp_path: Path) -> None:
    plan = _make_plan(
        tmp_path,
        parser=_ForwardingParser(error_after_first_yield=True),
    )

    run_parser_lifecycle(plan)

    rows = [
        {k: v for k, v in json.loads(line).items() if k != "ts"}
        for line in (tmp_path / "logs" / "parser" / "test_parser.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert {
        "event": "query_started",
        "keyword": "python",
        "location": "Hamburg",
    } in rows
    assert {"event": "query_ended", "keyword": "python", "location": "Hamburg"} in rows
    failure_reports = list((tmp_path / "failures").glob("*.md"))
    assert len(failure_reports) == 1


def test_parser_dead_writes_one_failure_report_without_touching_classify_handoff(
    tmp_path: Path,
) -> None:
    plan = _make_plan(
        tmp_path,
        parser=_DiscoverCrashParser(),
        classify_handoff=_ExplosiveTruthinessHandoff(),
    )

    run_parser_lifecycle(plan)

    failure_reports = list((tmp_path / "failures").glob("*.md"))
    assert len(failure_reports) == 1


def test_parser_dead_preserves_failure_report_run_log_and_metrics_observations(
    tmp_path: Path,
) -> None:
    plan, display = _make_plan_with_display(
        tmp_path,
        parser=_DiscoverCrashParser(),
    )

    run_parser_lifecycle(plan)

    failure_report = next((tmp_path / "failures").glob("*.md"))
    failure_body = failure_report.read_text(encoding="utf-8")
    assert "parser:test_parser" in failure_body
    assert "RuntimeError" in failure_body
    assert "fatal error in parser" in failure_body
    assert "Traceback" in failure_body

    run_log_content = (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    assert "=== parser_test_parser" in run_log_content
    assert "traceback ===" in run_log_content
    assert "RuntimeError: fatal error in parser" in run_log_content
    assert "parsers_dead=1" in run_log_content

    phase_calls = [
        call
        for call in display.calls
        if call.method == "update_phase" and call.name == "parser test parser"
    ]
    assert phase_calls[-1].kwargs["phase"] == "dead"
    assert "discovered=0" in run_log_content
    assert "enrich_failed=0" in run_log_content
    assert "not_served_queries=0" in run_log_content
    assert "unparseable_dates=0" in run_log_content


def test_stall_watchdog_logs_one_stalled_event_and_stack_trace_via_lifecycle(
    tmp_path: Path,
) -> None:
    threshold_s = 0.05
    plan = _make_plan(
        tmp_path,
        parser=_SleepingParser(sleep_s=threshold_s * 4),
        stall_threshold_s=threshold_s,
    )

    run_parser_lifecycle(plan)

    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "parser" / "test_parser.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    stalled_rows = [row for row in rows if row.get("event") == "stalled"]
    assert stalled_rows
    assert all(row["last_event_age_s"] >= threshold_s for row in stalled_rows)
    run_log_content = (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    assert "traceback" in run_log_content
    assert "File " in run_log_content


def test_stall_watchdog_can_report_again_after_parser_progress_ends_silence(
    tmp_path: Path,
) -> None:
    threshold_s = 0.05
    plan = _make_plan(
        tmp_path,
        parser=_TwoSilencesAroundProgressParser(sleep_s=threshold_s * 4),
        stall_threshold_s=threshold_s,
    )

    run_parser_lifecycle(plan)

    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "parser" / "test_parser.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    stalled_rows = [row for row in rows if row.get("event") == "stalled"]
    assert len(stalled_rows) == 2
    assert all(row["last_event_age_s"] >= threshold_s for row in stalled_rows)


def test_lifecycle_records_not_served_and_completed_queries_without_parser_dead(
    tmp_path: Path,
) -> None:
    plan, display = _make_plan_with_display(
        tmp_path,
        parser=_LifecycleAccountingParser(),
        keywords=["python", "django"],
    )

    run_parser_lifecycle(plan)

    events_path = tmp_path / "logs" / "parser" / "test_parser.events.jsonl"
    event_rows = [
        {k: v for k, v in json.loads(line).items() if k != "ts"}
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    run_log_content = (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    assert "not_served_queries=1" in run_log_content
    assert "queries_done=2" in run_log_content
    assert "parsers_dead=0" in run_log_content
    assert event_rows == [
        {"event": "parser started"},
        {"event": "query_started", "keyword": "python", "location": "Hamburg"},
        {"event": "query_ended", "keyword": "python", "location": "Hamburg"},
        {"event": "query_started", "keyword": "django", "location": "Hamburg"},
        {"event": "query_ended", "keyword": "django", "location": "Hamburg"},
    ]

    phase_calls = [
        call
        for call in display.calls
        if call.method == "update_phase" and call.name == "parser test parser"
    ]
    assert phase_calls[-1].kwargs["phase"] == "done"
    assert all(call.kwargs["phase"] != "dead" for call in phase_calls)


def test_parser_summary_duration_is_nonnegative_in_run_log(
    tmp_path: Path, monkeypatch
) -> None:
    plan = _make_plan(
        tmp_path,
        parser=_ImmediateParser(),
    )
    monotonic_values = iter([5.0, 4.0, 3.0, 2.0])
    monkeypatch.setattr(
        "application_pipeline.parser_lifecycle.time.monotonic",
        lambda: next(monotonic_values),
    )

    run_parser_lifecycle(plan)

    run_log_content = (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    assert "=== parser_test_parser" in run_log_content
    assert "duration=0.0" in run_log_content


def test_parser_lifecycle_emits_one_parser_summary_section_per_parser(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    run_log = RunLog(logs_dir)
    card_store = load_card_store(tmp_path / "extracts.json")
    dedup = load_dedup(
        tmp_path / ".seen.json",
        card_store=card_store,
        run_log=run_log,
    )
    display = FakeStatusDisplay()
    metrics = RunMetrics(display, run_log=run_log)
    metrics.register_parser(
        "parser_a",
        order=0,
        total_queries=1,
        has_native_enrich=False,
    )
    metrics.register_parser(
        "parser_b",
        order=2,
        total_queries=1,
        has_native_enrich=False,
    )

    run_parser_lifecycle(
        ParserLifecyclePlan(
            parsers=[
                ParserLifecycleExecution(
                    parser=_ImmediateParser(),
                    parser_id="parser_a",
                    classify_handoff=_NoopHandoff(),
                ),
                ParserLifecycleExecution(
                    parser=_ImmediateParser(),
                    parser_id="parser_b",
                    classify_handoff=_NoopHandoff(),
                ),
            ],
            keywords=["python"],
            locations=[City("Hamburg")],
            collaborators=ParserLifecycleCollaborators(
                run_log=run_log,
                run_state=_RunState(),
                freshness=FreshnessGate(
                    anchored_today=date(2026, 6, 16),
                    max_listing_age_days=30,
                    dedup=dedup,
                    run_log=run_log,
                    card_store=card_store,
                ),
                prefilter=PreFilterGate(
                    blacklist=[],
                    dedup=dedup,
                    run_log=run_log,
                ),
                content_gate=ContentGate(run_log=run_log),
                dedup=dedup,
                dedup_counters=DedupCounters(display=display, run_log=run_log),
                pool=Pool(),
                metrics=metrics,
                card_store=card_store,
                failure_report_writer=FailureReportWriter(tmp_path / "failures"),
            ),
        )
    )

    run_log_content = (logs_dir / "run.log").read_text(encoding="utf-8")
    assert run_log_content.count("=== parser_parser_a") == 1
    assert run_log_content.count("=== parser_parser_b") == 1
