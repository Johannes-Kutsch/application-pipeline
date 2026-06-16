from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from fake_status_display import FakeStatusDisplay

from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import load as load_dedup
from application_pipeline.dedup_counters import DedupCounters
from application_pipeline.failure_report import FailureReportWriter
from application_pipeline.extracts.card_store import load_card_store
from application_pipeline.freshness_gate import FreshnessGate
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


class _CollectingHandoff:
    def submit_ready(
        self,
        *,
        listing_id: int,
        stub: PositionStub,
        raw_description: str,
        parser_id: str,
    ) -> None:
        pass


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


def _make_plan(
    tmp_path: Path,
    *,
    parser: Parser,
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
                classify_handoff=_CollectingHandoff(),
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
                classify_handoff=_CollectingHandoff(),
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

    assert sum(1 for row in rows if row.get("event") == "stalled") == 1
    run_log_content = (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    assert "traceback" in run_log_content
    assert "File " in run_log_content


def test_lifecycle_records_not_served_and_completed_queries_without_parser_dead(
    tmp_path: Path,
) -> None:
    plan, display = _make_plan_with_display(
        tmp_path,
        parser=_LifecycleAccountingParser(),
        keywords=["python", "django"],
    )

    run_parser_lifecycle(plan)

    run_log_content = (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    assert "not_served_queries=1" in run_log_content
    assert "queries_done=2" in run_log_content
    assert "parsers_dead=0" in run_log_content

    phase_calls = [
        call
        for call in display.calls
        if call.method == "update_phase" and call.name == "parser test parser"
    ]
    assert phase_calls[-1].kwargs["phase"] == "done"
    assert all(call.kwargs["phase"] != "dead" for call in phase_calls)
