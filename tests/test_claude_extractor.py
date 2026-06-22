"""Tests for ClaudeExtractor v2 call shapes - classify_relevance and judge_top_n."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import pytest

from application_pipeline import ClassifyItem, Config, SourceEntry
from application_pipeline.llm import (
    ClaudeExtractor,
    CallUsage,
    ClaudeUsageLimitError,
    ExtractorBatchMalformedError,
    ExtractorMalformedJSONError,
    ExtractorUnreachableError,
)
from application_pipeline.llm.agent_runtime_invocation import (
    AgentRuntimeInvocationResult,
)
from application_pipeline.llm.types import (
    JudgeCandidate,
    MatchVerdict,
    RelevanceVerdict,
)
from application_pipeline.parser_log import RunLog
from application_pipeline.prompts import (
    CLASSIFY_RELEVANCE_SLOTS,
    JUDGE_TOP_N_SLOTS,
    PromptTemplate,
    Prompts,
)
# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


def _config() -> Config:
    return Config(
        sources=[SourceEntry(parser_type="bundesagentur")],
        locations=["Hamburg"],
    )


def _prompts() -> Prompts:
    return Prompts(
        classify_relevance=PromptTemplate(
            "v2 {LISTINGS}",
            CLASSIFY_RELEVANCE_SLOTS,
        ),
        judge_top_n=PromptTemplate("v2 {CANDIDATES}", JUDGE_TOP_N_SLOTS),
    )


def _usage() -> CallUsage:
    return CallUsage(
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=0,
        cost_usd=0.001,
        duration_s=0.5,
    )


def _judge_usage() -> CallUsage:
    return CallUsage(
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=0,
        cost_usd=0.003,
        duration_s=2.0,
    )


def _runtime_result(
    output: str,
    usage: CallUsage | None = None,
    log_path: Path | None = None,
) -> AgentRuntimeInvocationResult:
    return AgentRuntimeInvocationResult(
        kind="completed",
        output=output,
        log_path=log_path or Path("llm-classify.log"),
        usage=usage or _usage(),
        reset_time=None,
        message=None,
    )


def _classify_output(verdict: object) -> str:
    return f'<verdict id="1">{json.dumps(verdict)}</verdict>'


def _judge_output(verdicts: list[dict[str, int]]) -> str:
    return f"<verdicts>{json.dumps(verdicts)}</verdicts>"


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch, result: AgentRuntimeInvocationResult
) -> None:
    monkeypatch.setattr(
        "application_pipeline.llm.claude.invoke_agent_runtime",
        lambda *args, **kwargs: result,
    )


def _item(**kwargs: object) -> ClassifyItem:
    defaults: dict[str, object] = dict(
        title="Senior Python Engineer", raw_description="Python ML role"
    )
    defaults.update(kwargs)
    return ClassifyItem(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# classify_relevance: matched happy path
# ---------------------------------------------------------------------------


def test_classify_relevance_matched_returns_header_and_summary(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(
        monkeypatch,
        _runtime_result(
            _classify_output(
                {
                    "matches": True,
                    "header": "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01",
                    "summary": "Great role for ML engineers.",
                }
            )
        ),
    )
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    results, usage = extractor.classify_relevance(
        [_item(company="Acme", location="Hamburg")]
    )
    result = results[0]
    assert isinstance(result, RelevanceVerdict)
    assert result.matches is True
    assert (
        result.header == "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01"
    )
    assert result.summary == "Great role for ML engineers."
    assert usage.input_tokens == 100
    assert usage.cost_usd == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# classify_relevance: out-of-domain
# ---------------------------------------------------------------------------


def test_classify_relevance_out_of_domain_returns_none_header_and_summary(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch, _runtime_result(_classify_output({"matches": False})))
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    results, _ = extractor.classify_relevance([_item()])
    result = results[0]
    assert isinstance(result, RelevanceVerdict)
    assert result.matches is False
    assert result.header is None
    assert result.summary is None


# ---------------------------------------------------------------------------
# classify_relevance: malformed responses → None (batch protocol)
# ---------------------------------------------------------------------------


def test_classify_relevance_matched_missing_header_returns_none(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(
        monkeypatch,
        _runtime_result(_classify_output({"matches": True, "summary": "ok"})),
    )
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    results, _ = extractor.classify_relevance([_item()])
    assert results[0] is None


def test_classify_relevance_matched_missing_summary_returns_none(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(
        monkeypatch,
        _runtime_result(_classify_output({"matches": True, "header": "some header"})),
    )
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    results, _ = extractor.classify_relevance([_item()])
    assert results[0] is None


def test_classify_relevance_matched_empty_header_returns_none(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(
        monkeypatch,
        _runtime_result(
            _classify_output({"matches": True, "header": "", "summary": "ok"})
        ),
    )
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    results, _ = extractor.classify_relevance([_item()])
    assert results[0] is None


# ---------------------------------------------------------------------------
# classify_relevance: transport errors still raise
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# classify_relevance: prompt receives pre-fill fields
# ---------------------------------------------------------------------------


def test_classify_relevance_prompt_includes_company_and_location(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(
        monkeypatch,
        _runtime_result(
            _classify_output({"matches": True, "header": "h", "summary": "s"})
        ),
    )
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    extractor.classify_relevance([_item(company="TestCorp", location="Berlin")])
    prompt_sent = _read_transcripts(run_log, "llm_classify_relevance")[0]["prompt"]
    assert "TestCorp" in prompt_sent
    assert "Berlin" in prompt_sent


def test_classify_relevance_legacy_in_domain_field_returns_none(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(
        monkeypatch,
        _runtime_result(
            _classify_output({"in_domain": True, "header": "h", "summary": "s"})
        ),
    )
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    results, _ = extractor.classify_relevance([_item()])
    assert results[0] is None


# ---------------------------------------------------------------------------
# judge_top_n: happy path
# ---------------------------------------------------------------------------


def _make_candidates(n: int) -> list[JudgeCandidate]:
    return [
        JudgeCandidate(id=i, header=f"Title {i}\nCo", summary=f"Summary {i}")
        for i in range(n)
    ]


def test_judge_top_n_returns_match_verdict_with_id_and_rank(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = _make_candidates(5)
    verdicts_raw = [{"id": c.id, "rank": i + 1} for i, c in enumerate(candidates)]
    _patch_runtime(
        monkeypatch,
        _runtime_result(_judge_output(verdicts_raw), usage=_judge_usage()),
    )
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    results, usage = extractor.judge_top_n(candidates)
    assert len(results) == 5
    assert all(isinstance(v, MatchVerdict) for v in results)
    assert {v.rank for v in results} == {1, 2, 3, 4, 5}
    assert all(v.id in {c.id for c in candidates} for v in results)
    assert usage.cost_usd == pytest.approx(0.003)


def test_judge_top_n_empty_candidates_returns_empty_list(
    run_log: RunLog,
) -> None:
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    results, usage = extractor.judge_top_n([])
    assert results == []
    assert usage.cost_usd == pytest.approx(0.0)
    assert _read_transcripts(run_log, "llm_judge_match") == []
    assert _read_events(run_log, "llm_judge_match") == []


def test_judge_top_n_candidates_appear_in_prompt(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = _make_candidates(2)
    verdicts_raw = [{"id": c.id, "rank": i + 1} for i, c in enumerate(candidates)]
    _patch_runtime(
        monkeypatch,
        _runtime_result(_judge_output(verdicts_raw), usage=_judge_usage()),
    )
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    extractor.judge_top_n(candidates)
    prompt_sent = _read_transcripts(run_log, "llm_judge_match")[0]["prompt"]
    assert "[Candidate id=0]" in prompt_sent
    assert "[Candidate id=1]" in prompt_sent


def test_judge_top_n_coerces_string_id_to_int(
    run_log: RunLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = _make_candidates(3)
    _patch_runtime(
        monkeypatch,
        _runtime_result(
            '<verdicts>[{"id": "0", "rank": 1}]</verdicts>',
            usage=_judge_usage(),
        ),
    )
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    verdicts, _ = extractor.judge_top_n(candidates)
    assert len(verdicts) == 1
    assert verdicts[0].id == 0
    assert verdicts[0].rank == 1


def test_judge_top_n_rejects_non_numeric_string_verdict_id(
    run_log: RunLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = _make_candidates(3)
    _patch_runtime(
        monkeypatch,
        _runtime_result(
            '<verdicts>[{"id": "abc", "rank": 1}]</verdicts>',
            usage=_judge_usage(),
        ),
    )
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.judge_top_n(candidates)


def test_judge_top_n_via_agent_runtime_keeps_candidate_block_shape_and_logs_judge_runtime_file(
    run_log: RunLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def _fake_invoke(
        prompt: str, *, logs_root: Path, call_site: str, provider_auth: object = None
    ) -> AgentRuntimeInvocationResult:
        assert call_site == "judge"
        captured["prompt"] = prompt
        captured["call_site"] = call_site
        runtime_log = (
            logs_root / "llm" / "agent-runtime" / "judge" / "llm-judge-complete.log"
        )
        runtime_log.parent.mkdir(parents=True, exist_ok=True)
        runtime_log.write_text("runtime judge output\n", encoding="utf-8")
        captured["runtime_log_path"] = runtime_log
        return AgentRuntimeInvocationResult(
            kind="completed",
            output=(
                '<verdicts>[{"id": 0, "rank": 1}, {"id": 1, "rank": 2}]</verdicts>'
            ),
            log_path=runtime_log,
            usage=CallUsage(
                input_tokens=15,
                output_tokens=3,
                cache_read_tokens=1,
                cost_usd=0.002,
                duration_s=0.9,
            ),
            reset_time=None,
            message=None,
        )

    monkeypatch.setattr(
        "application_pipeline.llm.claude.invoke_agent_runtime", _fake_invoke
    )

    candidates = [
        JudgeCandidate(
            id=0, header="Title 0\nACME · Hamburg · remote", summary="Summary 0"
        ),
        JudgeCandidate(
            id=1, header="Title 1\nACME · Berlin · remote", summary="Summary 1"
        ),
    ]
    extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
    results, usage = extractor.judge_top_n(candidates)

    assert captured["call_site"] == "judge"
    prompt = str(captured["prompt"])
    assert "[Candidate id=0]" in prompt
    assert "Title 0" in prompt
    assert "Summary 0" in prompt
    assert "[Candidate id=1]" in prompt
    assert "Title 1" in prompt
    assert "Summary 1" in prompt
    runtime_log_path = captured["runtime_log_path"]
    assert isinstance(runtime_log_path, Path)
    assert runtime_log_path.exists()
    assert (
        runtime_log_path.parent == run_log.logs_dir / "llm" / "agent-runtime" / "judge"
    )
    assert len(results) == 2
    assert usage.input_tokens == 15
    assert usage.output_tokens == 3


def test_judge_top_n_via_agent_runtime_usage_limit_becomes_quota_error(
    run_log: RunLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_invoke(
        prompt: str, *, logs_root: Path, call_site: str, provider_auth: object = None
    ) -> AgentRuntimeInvocationResult:
        return AgentRuntimeInvocationResult(
            kind="usage_limit",
            output="quota reached",
            log_path=logs_root
            / "llm"
            / "agent-runtime"
            / "judge"
            / "llm-judge-quota.log",
            usage=CallUsage(
                input_tokens=11,
                output_tokens=0,
                cache_read_tokens=0,
                cost_usd=0.0,
                duration_s=0.0,
            ),
            reset_time=None,
            message=None,
        )

    monkeypatch.setattr(
        "application_pipeline.llm.claude.invoke_agent_runtime", _fake_invoke
    )

    with pytest.raises(ClaudeUsageLimitError) as excinfo:
        extractor = ClaudeExtractor(_config(), _prompts(), run_log=run_log)
        extractor.judge_top_n([JudgeCandidate(id=0, header="h", summary="s")])

    assert "usage limit" in str(excinfo.value).lower()


def _read_transcripts(run_log: RunLog, component_id: str) -> list[dict]:  # type: ignore[type-arg]
    path = (
        run_log.logs_dir
        / "llm"
        / f"{component_id.removeprefix('llm_')}.transcripts.jsonl"
    )
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _read_events(run_log: RunLog, component_id: str) -> list[dict]:  # type: ignore[type-arg]
    path = (
        run_log.logs_dir / "llm" / f"{component_id.removeprefix('llm_')}.events.jsonl"
    )
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# ---------------------------------------------------------------------------
# classify_relevance: batch protocol — single call for N items
# ---------------------------------------------------------------------------


def _batch_prompts() -> Prompts:
    """Prompts using the new LISTINGS slot for batch classify."""
    return Prompts(
        classify_relevance=PromptTemplate(
            "batch {LISTINGS}",
            frozenset({"LISTINGS"}),
        ),
        judge_top_n=PromptTemplate("v2 {CANDIDATES}", JUDGE_TOP_N_SLOTS),
    )


def _batch_classify_output(
    id_verdict_pairs: list[tuple[int, object]],
) -> str:
    parts = [
        f'<verdict id="{id_}">{json.dumps(verdict)}</verdict>'
        for id_, verdict in id_verdict_pairs
    ]
    return "\n".join(parts)


def test_classify_relevance_batch_prompt_includes_sequential_ids(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_item(title=f"Job {i + 1}") for i in range(3)]
    output = _batch_classify_output(
        [(1, {"matches": False}), (2, {"matches": False}), (3, {"matches": False})]
    )
    _patch_runtime(monkeypatch, _runtime_result(output))
    extractor = ClaudeExtractor(_config(), _batch_prompts(), run_log=run_log)
    extractor.classify_relevance(items)
    prompt_sent = _read_transcripts(run_log, "llm_classify_relevance")[0]["prompt"]
    assert "id=1" in prompt_sent
    assert "id=2" in prompt_sent
    assert "id=3" in prompt_sent


def test_classify_relevance_out_of_order_verdicts_map_to_correct_positions(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_item(title=f"Job {i + 1}") for i in range(3)]
    # verdicts arrive as id=3, id=1, id=2
    output = _batch_classify_output(
        [
            (3, {"matches": True, "header": "h3", "summary": "s3"}),
            (1, {"matches": True, "header": "h1", "summary": "s1"}),
            (2, {"matches": False}),
        ]
    )
    _patch_runtime(monkeypatch, _runtime_result(output))
    extractor = ClaudeExtractor(_config(), _batch_prompts(), run_log=run_log)
    results, _ = extractor.classify_relevance(items)
    assert len(results) == 3
    assert results[0] is not None and results[0].header == "h1"
    assert results[1] is not None and results[1].matches is False
    assert results[2] is not None and results[2].header == "h3"


def test_classify_relevance_missing_verdict_tag_produces_none(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_item(title="Job 1"), _item(title="Job 2")]
    # only id=1 present; id=2 missing
    output = _batch_classify_output([(1, {"matches": False})])
    _patch_runtime(monkeypatch, _runtime_result(output))
    extractor = ClaudeExtractor(_config(), _batch_prompts(), run_log=run_log)
    results, _ = extractor.classify_relevance(items)
    assert results[0] is not None and results[0].matches is False
    assert results[1] is None


def test_classify_relevance_malformed_verdict_json_produces_none(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_item(title="Job 1"), _item(title="Job 2")]
    output = (
        '<verdict id="1">{"matches": false}</verdict>'
        '<verdict id="2">not valid json</verdict>'
    )
    _patch_runtime(monkeypatch, _runtime_result(output))
    extractor = ClaudeExtractor(_config(), _batch_prompts(), run_log=run_log)
    results, _ = extractor.classify_relevance(items)
    assert results[0] is not None and results[0].matches is False
    assert results[1] is None


def test_classify_relevance_all_verdicts_missing_returns_all_none_no_error(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_item(), _item()]
    _patch_runtime(monkeypatch, _runtime_result("no verdict tags here"))
    extractor = ClaudeExtractor(_config(), _batch_prompts(), run_log=run_log)
    results, _ = extractor.classify_relevance(items)
    assert results == [None, None]


def test_classify_relevance_usage_reflects_single_call_tokens(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_item() for _ in range(3)]
    output = (
        '<verdict id="1">{"matches": false}</verdict>'
        '<verdict id="2">{"matches": false}</verdict>'
        '<verdict id="3">{"matches": false}</verdict>'
    )
    _patch_runtime(
        monkeypatch,
        _runtime_result(
            output,
            usage=CallUsage(
                input_tokens=500,
                output_tokens=60,
                cache_read_tokens=200,
                cost_usd=0.01,
                duration_s=2.5,
            ),
        ),
    )
    extractor = ClaudeExtractor(_config(), _batch_prompts(), run_log=run_log)
    _, usage = extractor.classify_relevance(items)
    assert usage.input_tokens == 500
    assert usage.output_tokens == 60
    assert usage.cache_read_tokens == 200
    assert usage.cost_usd == pytest.approx(0.01)


def test_classify_relevance_batch_logs_the_full_batch_prompt_and_response(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_item(title=f"Job {i + 1}") for i in range(3)]
    output = _batch_classify_output(
        [(1, {"matches": False}), (2, {"matches": False}), (3, {"matches": False})]
    )
    _patch_runtime(monkeypatch, _runtime_result(output))
    extractor = ClaudeExtractor(_config(), _batch_prompts(), run_log=run_log)
    extractor.classify_relevance(items)
    transcript = _read_transcripts(run_log, "llm_classify_relevance")[-1]
    assert "Job 1" in transcript["prompt"]
    assert "Job 2" in transcript["prompt"]
    assert "Job 3" in transcript["prompt"]
    assert transcript["raw_response"] == output


def test_classify_relevance_via_agent_runtime_keeps_verdict_shape_and_outcomes(
    run_log: RunLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_invoke(
        prompt: str, *, logs_root: Path, call_site: str, provider_auth: object = None
    ) -> AgentRuntimeInvocationResult:
        captured["prompt"] = prompt
        captured["call_site"] = call_site
        runtime_log = (
            logs_root / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
        )
        captured["runtime_log_path"] = runtime_log
        runtime_log.parent.mkdir(parents=True, exist_ok=True)
        runtime_log.write_text("runtime output\n", encoding="utf-8")
        return AgentRuntimeInvocationResult(
            kind="completed",
            output=(
                '<verdict id="1">'
                '{ "matches": true, "header": "Header", "summary": "Match" }'
                "</verdict>"
                '<verdict id="2">{ "matches": false }</verdict>'
                '<verdict id="3">'
                '{ "matches": true, "header": "", "summary": "Missing header" }'
                "</verdict>"
            ),
            log_path=runtime_log,
            usage=CallUsage(
                input_tokens=11,
                output_tokens=7,
                cache_read_tokens=3,
                cost_usd=0.25,
                duration_s=1.5,
            ),
            reset_time=None,
            message=None,
        )

    monkeypatch.setattr(
        "application_pipeline.llm.claude.invoke_agent_runtime",
        _fake_invoke,
    )

    items = [_item(title=f"Job {i + 1}") for i in range(3)]
    extractor = ClaudeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
    )
    results, usage = extractor.classify_relevance(items)

    assert captured["call_site"] == "classify"
    assert "id=1" in str(captured["prompt"])
    assert "id=2" in str(captured["prompt"])
    assert "id=3" in str(captured["prompt"])
    runtime_log_path = captured["runtime_log_path"]
    assert isinstance(runtime_log_path, Path)
    assert (
        runtime_log_path.parent
        == run_log.logs_dir / "llm" / "agent-runtime" / "classify"
    )
    assert runtime_log_path.exists()
    assert results[0] is not None and results[0].matches is True
    assert results[1] is not None and results[1].matches is False
    assert results[2] is None
    assert usage.input_tokens == 11
    assert usage.output_tokens == 7
    assert usage.cache_read_tokens == 3


def test_classify_relevance_via_agent_runtime_usage_limit_becomes_quota_error(
    run_log: RunLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_invoke(
        prompt: str, *, logs_root: Path, call_site: str, provider_auth: object = None
    ) -> AgentRuntimeInvocationResult:
        assert call_site == "classify"
        runtime_log = (
            logs_root / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
        )
        runtime_log.parent.mkdir(parents=True, exist_ok=True)
        runtime_log.write_text("usage limited\n", encoding="utf-8")
        return AgentRuntimeInvocationResult(
            kind="usage_limit",
            output="limit reached",
            log_path=runtime_log,
            usage=CallUsage(
                input_tokens=11,
                output_tokens=7,
                cache_read_tokens=3,
                cost_usd=0.25,
                duration_s=1.5,
            ),
            reset_time=datetime(2026, 6, 22, 8, 45, tzinfo=timezone.utc),
            message=None,
        )

    monkeypatch.setattr(
        "application_pipeline.llm.claude.invoke_agent_runtime",
        _fake_invoke,
    )

    extractor = ClaudeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
    )

    with pytest.raises(ClaudeUsageLimitError) as excinfo:
        extractor.classify_relevance([_item()])

    assert excinfo.value.reset_time == datetime(2026, 6, 22, 8, 45, tzinfo=timezone.utc)


def test_classify_relevance_via_agent_runtime_retryable_failure_marks_items_retryable(
    run_log: RunLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_invoke(
        prompt: str, *, logs_root: Path, call_site: str, provider_auth: object = None
    ) -> AgentRuntimeInvocationResult:
        assert call_site == "classify"
        runtime_log = (
            logs_root / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
        )
        runtime_log.parent.mkdir(parents=True, exist_ok=True)
        runtime_log.write_text("provider flake\n", encoding="utf-8")
        return AgentRuntimeInvocationResult(
            kind="retryable_provider_failure",
            output="provider flake",
            log_path=runtime_log,
            usage=CallUsage(
                input_tokens=5,
                output_tokens=1,
                cache_read_tokens=0,
                cost_usd=0.01,
                duration_s=0.3,
            ),
            reset_time=None,
            message=None,
        )

    monkeypatch.setattr(
        "application_pipeline.llm.claude.invoke_agent_runtime",
        _fake_invoke,
    )

    extractor = ClaudeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
    )
    results, usage = extractor.classify_relevance([_item(), _item()])

    assert results == [None, None]
    assert usage.input_tokens == 5
    assert usage.output_tokens == 1
    assert usage.cache_read_tokens == 0
    assert usage.cost_usd == pytest.approx(0.01)


def test_classify_relevance_via_agent_runtime_missing_usage_is_malformed(
    run_log: RunLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_invoke(
        prompt: str, *, logs_root: Path, call_site: str, provider_auth: object = None
    ) -> AgentRuntimeInvocationResult:
        assert prompt
        assert call_site == "classify"
        runtime_log = (
            logs_root / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
        )
        runtime_log.parent.mkdir(parents=True, exist_ok=True)
        runtime_log.write_text("missing usage\n", encoding="utf-8")
        return AgentRuntimeInvocationResult(
            kind="missing_usage",
            output='<verdict id="1">{ "matches": false }</verdict>',
            log_path=runtime_log,
            usage=None,
            reset_time=None,
            message=None,
        )

    monkeypatch.setattr(
        "application_pipeline.llm.claude.invoke_agent_runtime",
        _fake_invoke,
    )

    extractor = ClaudeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
    )

    with pytest.raises(ExtractorMalformedJSONError) as excinfo:
        extractor.classify_relevance([_item()])

    assert (
        excinfo.value.raw_response == '<verdict id="1">{ "matches": false }</verdict>'
    )


def test_classify_relevance_via_agent_runtime_hard_provider_failure_is_unreachable(
    run_log: RunLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_invoke(
        prompt: str, *, logs_root: Path, call_site: str, provider_auth: object = None
    ) -> AgentRuntimeInvocationResult:
        assert prompt
        assert call_site == "classify"
        runtime_log = (
            logs_root / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
        )
        runtime_log.parent.mkdir(parents=True, exist_ok=True)
        runtime_log.write_text("hard failure\n", encoding="utf-8")
        return AgentRuntimeInvocationResult(
            kind="hard_provider_failure",
            output="runtime failed",
            log_path=runtime_log,
            usage=None,
            reset_time=None,
            message="provider exploded",
        )

    monkeypatch.setattr(
        "application_pipeline.llm.claude.invoke_agent_runtime",
        _fake_invoke,
    )

    extractor = ClaudeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
    )

    with pytest.raises(ExtractorUnreachableError) as excinfo:
        extractor.classify_relevance([_item()])

    assert excinfo.value.stderr == "provider exploded"
