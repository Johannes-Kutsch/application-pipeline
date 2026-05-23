"""Tests for gates_bundle.run_gates() pre-enrich invocation."""

from __future__ import annotations

from unittest.mock import MagicMock

from application_pipeline.gates_bundle import run_gates
from application_pipeline.parsers.types import PositionStub


def _stub(
    *, url: str = "https://example.com/job", title: str = "Python Dev"
) -> PositionStub:
    return PositionStub(url=url, title=title, source="test")


def _gates(
    *,
    dedup_result: str = "miss",
    prefilter_pass: bool = True,
    freshness_pass: bool = True,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    run_log = MagicMock()
    metrics = MagicMock()
    dedup_counters = MagicMock()
    dedup = MagicMock()
    dedup.is_seen.return_value = dedup_result
    prefilter = MagicMock()
    prefilter.admit_stub.return_value = prefilter_pass
    freshness = MagicMock()
    freshness.admit_stub.return_value = freshness_pass
    content = MagicMock()
    return run_log, metrics, dedup_counters, dedup, prefilter, freshness, content


# ---------------------------------------------------------------------------
# AC1: dedup-hit stubs return "drop"
# ---------------------------------------------------------------------------


def test_dedup_url_hit_returns_drop() -> None:
    """A stub already in the dedup store (url_hit) must be dropped before enrich."""
    run_log, metrics, dedup_counters, dedup, prefilter, freshness, content = _gates(
        dedup_result="url_hit"
    )

    verdict = run_gates(
        _stub(),
        run_log=run_log,
        metrics=metrics,
        dedup_counters=dedup_counters,
        dedup=dedup,
        prefilter=prefilter,
        freshness=freshness,
        content=content,
    )

    assert verdict == "drop"


def test_dedup_tuple_hit_returns_drop() -> None:
    """A stub matched by company/title/location tuple (tuple_hit) must be dropped before enrich."""
    run_log, metrics, dedup_counters, dedup, prefilter, freshness, content = _gates(
        dedup_result="tuple_hit"
    )

    verdict = run_gates(
        _stub(),
        run_log=run_log,
        metrics=metrics,
        dedup_counters=dedup_counters,
        dedup=dedup,
        prefilter=prefilter,
        freshness=freshness,
        content=content,
    )

    assert verdict == "drop"


def test_dedup_run_hit_returns_drop() -> None:
    """A stub already seen in this run (run_hit) must be dropped before enrich."""
    run_log, metrics, dedup_counters, dedup, prefilter, freshness, content = _gates(
        dedup_result="run_hit"
    )

    verdict = run_gates(
        _stub(),
        run_log=run_log,
        metrics=metrics,
        dedup_counters=dedup_counters,
        dedup=dedup,
        prefilter=prefilter,
        freshness=freshness,
        content=content,
    )

    assert verdict == "drop"


def test_dedup_judge_pending_returns_judge_pending() -> None:
    """A stub with matched status in dedup (judge_pending) must signal pool_collector."""
    run_log, metrics, dedup_counters, dedup, prefilter, freshness, content = _gates(
        dedup_result="judge_pending"
    )

    verdict = run_gates(
        _stub(),
        run_log=run_log,
        metrics=metrics,
        dedup_counters=dedup_counters,
        dedup=dedup,
        prefilter=prefilter,
        freshness=freshness,
        content=content,
    )

    assert verdict == "judge_pending"


# ---------------------------------------------------------------------------
# AC2: blacklisted title drops before enrich
# ---------------------------------------------------------------------------


def test_blacklisted_title_returns_drop() -> None:
    """A stub whose title matches a NEGATIVE_KEYWORDS entry must be dropped before enrich."""
    run_log, metrics, dedup_counters, dedup, prefilter, freshness, content = _gates(
        prefilter_pass=False
    )

    verdict = run_gates(
        _stub(title="Senior Recruiter"),
        run_log=run_log,
        metrics=metrics,
        dedup_counters=dedup_counters,
        dedup=dedup,
        prefilter=prefilter,
        freshness=freshness,
        content=content,
    )

    assert verdict == "drop"


# ---------------------------------------------------------------------------
# AC3: stale posted_date drops before enrich
# ---------------------------------------------------------------------------


def test_stale_stub_returns_drop() -> None:
    """A stub with posted_date older than MAX_LISTING_AGE_DAYS must be dropped before enrich."""
    run_log, metrics, dedup_counters, dedup, prefilter, freshness, content = _gates(
        freshness_pass=False
    )

    verdict = run_gates(
        _stub(),
        run_log=run_log,
        metrics=metrics,
        dedup_counters=dedup_counters,
        dedup=dedup,
        prefilter=prefilter,
        freshness=freshness,
        content=content,
    )

    assert verdict == "drop"


# ---------------------------------------------------------------------------
# AC4: passing stub returns "pass"
# ---------------------------------------------------------------------------


def test_all_gates_pass_returns_pass() -> None:
    """A stub that passes all pre-enrich gates must return 'pass' so enrich is called."""
    run_log, metrics, dedup_counters, dedup, prefilter, freshness, content = _gates()

    verdict = run_gates(
        _stub(),
        run_log=run_log,
        metrics=metrics,
        dedup_counters=dedup_counters,
        dedup=dedup,
        prefilter=prefilter,
        freshness=freshness,
        content=content,
    )

    assert verdict == "pass"


def test_dedup_record_called_for_miss() -> None:
    """On a dedup miss, dedup_counters.record() must be called with the dedup result."""
    run_log, metrics, dedup_counters, dedup, prefilter, freshness, content = _gates(
        dedup_result="miss"
    )

    run_gates(
        _stub(),
        run_log=run_log,
        metrics=metrics,
        dedup_counters=dedup_counters,
        dedup=dedup,
        prefilter=prefilter,
        freshness=freshness,
        content=content,
    )

    dedup_counters.record.assert_called_once_with("miss")


def test_dedup_record_called_for_url_hit() -> None:
    """On a dedup url_hit, dedup_counters.record() must still be called."""
    run_log, metrics, dedup_counters, dedup, prefilter, freshness, content = _gates(
        dedup_result="url_hit"
    )

    run_gates(
        _stub(),
        run_log=run_log,
        metrics=metrics,
        dedup_counters=dedup_counters,
        dedup=dedup,
        prefilter=prefilter,
        freshness=freshness,
        content=content,
    )

    dedup_counters.record.assert_called_once_with("url_hit")
