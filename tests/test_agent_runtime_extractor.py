"""Tests for AgentRuntimeExtractor behavior via classify_relevance and judge_top_n."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from collections.abc import Callable
import pytest
from agent_runtime.runtime import InvocationRecord, RuntimeOutcome
from agent_runtime.runtime import ProviderAuth
from agent_runtime.session import RunKind

from application_pipeline import ClassifyItem, Config, SourceEntry
from application_pipeline.llm import (
    AgentRuntimeExtractor,
    AgentRuntimeCallSiteName,
    AgentRuntimeInvocationAdapter,
    AgentRuntimeInvocationPort,
    AgentRuntimeInvocationResult,
    ExtractorBatchMalformedError,
    ExtractorUnreachableError,
    UsageLimitError,
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


def _runtime_result(
    output: str,
    evidence_dir: Path | None = None,
) -> AgentRuntimeInvocationResult:
    return AgentRuntimeInvocationResult(
        kind="completed",
        output=output,
        evidence_dir=evidence_dir or Path("llm-classify.log"),
        reset_time=None,
        message=None,
    )


def _classify_output(verdict: object) -> str:
    return f'<verdict id="1">{json.dumps(verdict)}</verdict>'


def _judge_output(verdicts: list[dict[str, int]]) -> str:
    return f"<verdicts>{json.dumps(verdicts)}</verdicts>"


class _MockInvocationPort:
    def __init__(
        self,
        *,
        result: AgentRuntimeInvocationResult | None = None,
        invoke: Callable[..., AgentRuntimeInvocationResult] | None = None,
    ) -> None:
        self._result = result
        self._invoke = invoke
        self.calls: list[dict[str, object]] = []

    def invoke(
        self,
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        self.calls.append(
            {
                "prompt": prompt,
                "logs_root": logs_root,
                "call_site": call_site,
                "provider_auth": provider_auth,
            }
        )
        if self._invoke is not None:
            return self._invoke(
                prompt,
                logs_root=logs_root,
                call_site=call_site,
                provider_auth=provider_auth,
            )
        assert self._result is not None
        return self._result


def _invocation_port(result: AgentRuntimeInvocationResult) -> _MockInvocationPort:
    return _MockInvocationPort(result=result)


def _capturing_invocation_port(
    result: AgentRuntimeInvocationResult,
    captured: dict[str, object],
) -> _MockInvocationPort:
    def _invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        captured["prompt"] = prompt
        captured["logs_root"] = logs_root
        captured["call_site"] = call_site
        captured["provider_auth"] = provider_auth
        return result

    return _MockInvocationPort(invoke=_invoke)


def _item(**kwargs: object) -> ClassifyItem:
    defaults: dict[str, object] = dict(
        title="Senior Python Engineer", raw_description="Python ML role"
    )
    defaults.update(kwargs)
    return ClassifyItem(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# classify_relevance: matched happy path
# ---------------------------------------------------------------------------


def test_classify_relevance_exposes_evidence_directory_as_last_log_path(
    run_log: RunLog,
) -> None:
    evidence_dir = (
        run_log.logs_dir / "llm" / "agent-runtime" / "classify" / "llm-classify-1"
    )
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(
                _classify_output({"matches": False}), evidence_dir=evidence_dir
            )
        ),
    )

    extractor.classify_relevance([_item()])

    assert extractor.last_classify_log_path == evidence_dir


def test_classify_relevance_matched_returns_header_and_summary(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(
                _classify_output(
                    {
                        "matches": True,
                        "header": "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01",
                        "summary": "Great role for ML engineers.",
                    }
                )
            )
        ),
    )
    results = extractor.classify_relevance([_item(company="Acme", location="Hamburg")])
    result = results[0]
    assert isinstance(result, RelevanceVerdict)
    assert result.matches is True
    assert (
        result.header == "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01"
    )
    assert result.summary == "Great role for ML engineers."


# ---------------------------------------------------------------------------
# classify_relevance: out-of-domain
# ---------------------------------------------------------------------------


def test_classify_relevance_out_of_domain_returns_none_header_and_summary(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(_classify_output({"matches": False}))
        ),
    )
    results = extractor.classify_relevance([_item()])
    result = results[0]
    assert isinstance(result, RelevanceVerdict)
    assert result.matches is False
    assert result.header is None
    assert result.summary is None


def test_classify_relevance_single_completed_legacy_verdict_through_invocation_port(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result('<verdict>{"matches": false}</verdict>')
        ),
    )

    results = extractor.classify_relevance([_item()])

    assert results == [RelevanceVerdict(matches=False)]


def test_classify_relevance_single_completed_legacy_matching_verdict_through_invocation_port(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(
                '<verdict>{"matches": true, "header": "h", "summary": "s"}</verdict>'
            )
        ),
    )

    results = extractor.classify_relevance([_item()])

    assert results == [RelevanceVerdict(matches=True, header="h", summary="s")]


# ---------------------------------------------------------------------------
# classify_relevance: malformed responses → None (batch protocol)
# ---------------------------------------------------------------------------


def test_classify_relevance_matched_missing_header_returns_none(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(_classify_output({"matches": True, "summary": "ok"}))
        ),
    )
    results = extractor.classify_relevance([_item()])
    assert results[0] is None


def test_classify_relevance_matched_missing_summary_returns_none(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(
                _classify_output({"matches": True, "header": "some header"})
            )
        ),
    )
    results = extractor.classify_relevance([_item()])
    assert results[0] is None


def test_classify_relevance_matched_empty_header_returns_none(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(
                _classify_output({"matches": True, "header": "", "summary": "ok"})
            )
        ),
    )
    results = extractor.classify_relevance([_item()])
    assert results[0] is None


def test_classify_relevance_single_legacy_matching_verdict_missing_summary_returns_none(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result('<verdict>{"matches": true, "header": "h"}</verdict>')
        ),
    )

    results = extractor.classify_relevance([_item()])

    assert results == [None]


def test_classify_relevance_single_completed_without_verdict_tag_returns_none(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result('```json\n{"matches": false}\n```')
        ),
    )

    results = extractor.classify_relevance([_item()])

    assert results == [None]


# ---------------------------------------------------------------------------
# classify_relevance: transport errors still raise
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# classify_relevance: prompt receives pre-fill fields
# ---------------------------------------------------------------------------


def test_classify_relevance_prompt_includes_company_and_location(
    run_log: RunLog,
) -> None:
    captured: dict[str, object] = {}
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_capturing_invocation_port(
            _runtime_result(
                _classify_output({"matches": True, "header": "h", "summary": "s"})
            ),
            captured,
        ),
    )
    extractor.classify_relevance([_item(company="TestCorp", location="Berlin")])
    prompt_sent = str(captured["prompt"])
    assert "TestCorp" in prompt_sent
    assert "Berlin" in prompt_sent
    assert not (
        run_log.logs_dir / "llm" / "classify_relevance.transcripts.jsonl"
    ).exists()


def test_classify_relevance_uses_agent_runtime_invocation_port(
    run_log: RunLog,
) -> None:
    provider_auth = ProviderAuth(opencode_api_key="operator-key")
    invocation_port = _invocation_port(
        _runtime_result(_classify_output({"matches": False}))
    )
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        provider_auth=provider_auth,
        invocation_port=invocation_port,
    )

    results = extractor.classify_relevance([_item(company="Acme", location="Hamburg")])

    assert results == [RelevanceVerdict(matches=False)]
    assert invocation_port.calls == [
        {
            "prompt": (
                "v2 ## Stellenanzeige id=1\n\n"
                "- Jobtitel: Senior Python Engineer\n"
                "- Unternehmen: Acme\n"
                "- Ort: Hamburg\n\n"
                "Python ML role"
            ),
            "logs_root": run_log.logs_dir,
            "call_site": "classify",
            "provider_auth": provider_auth,
        }
    ]


def test_classify_relevance_preserves_classify_call_facts_through_invocation_port(
    run_log: RunLog,
) -> None:
    evidence_dir = (
        run_log.logs_dir / "llm" / "agent-runtime" / "classify" / "llm-classify-7"
    )
    invocation_port = _invocation_port(
        _runtime_result(
            _classify_output({"matches": False}),
            evidence_dir=evidence_dir,
        )
    )
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=invocation_port,
    )

    results = extractor.classify_relevance(
        [
            _item(
                title="Senior Python Engineer",
                company="Acme",
                location="Hamburg",
                posted_date=date(2024, 1, 2),
                raw_description="First raw description",
            ),
            _item(
                title="ML Platform Engineer",
                raw_description="Second raw description",
            ),
        ]
    )

    assert results == [RelevanceVerdict(matches=False), None]
    assert extractor.last_classify_log_path == evidence_dir
    assert invocation_port.calls == [
        {
            "prompt": (
                "v2 ## Stellenanzeige id=1\n\n"
                "- Jobtitel: Senior Python Engineer\n"
                "- Unternehmen: Acme\n"
                "- Ort: Hamburg\n"
                "- Listing-Datum: 2024-01-02\n\n"
                "First raw description\n\n"
                "## Stellenanzeige id=2\n\n"
                "- Jobtitel: ML Platform Engineer\n\n"
                "Second raw description"
            ),
            "logs_root": run_log.logs_dir,
            "call_site": "classify",
            "provider_auth": None,
        }
    ]


def test_classify_relevance_legacy_in_domain_field_returns_none(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(
                _classify_output({"in_domain": True, "header": "h", "summary": "s"})
            )
        ),
    )
    results = extractor.classify_relevance([_item()])
    assert results[0] is None


def test_classify_relevance_does_not_write_pipeline_owned_transcript_jsonl(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(_classify_output({"matches": False}))
        ),
    )
    extractor.classify_relevance([_item()])

    transcript_path = run_log.logs_dir / "llm" / "classify_relevance.transcripts.jsonl"
    assert not transcript_path.exists()


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
) -> None:
    candidates = _make_candidates(5)
    verdicts_raw = [{"id": c.id, "rank": i + 1} for i, c in enumerate(candidates)]
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(_runtime_result(_judge_output(verdicts_raw))),
    )
    results = extractor.judge_top_n(candidates)
    assert len(results) == 5
    assert all(isinstance(v, MatchVerdict) for v in results)
    assert {v.rank for v in results} == {1, 2, 3, 4, 5}
    assert all(v.id in {c.id for c in candidates} for v in results)


def test_judge_top_n_preserves_completed_provider_output_through_invocation_port(
    run_log: RunLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeRuntimeClient:
        async def run_ephemeral(self, request: object) -> RuntimeOutcome:
            return RuntimeOutcome(
                kind="completed",
                output="",
                invocation_records=(
                    InvocationRecord(
                        run_kind=RunKind.FRESH,
                        service_name="opencode",
                        model="deepseek-v4-flash",
                        effort="medium",
                        outcome="completed",
                        provider_session_id="sess-1",
                        events=(),
                        provider_output=(
                            b'<verdicts>[{"id": 7, "rank": 1}]</verdicts>'
                        ),
                        usage=None,
                    ),
                ),
            )

    monkeypatch.setattr(
        "application_pipeline.llm.agent_runtime_invocation.RuntimeClient",
        _FakeRuntimeClient,
    )
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
    )

    results = extractor.judge_top_n(
        [
            JudgeCandidate(id=7, header="Header 7", summary="Summary 7"),
            JudgeCandidate(id=9, header="Header 9", summary="Summary 9"),
        ]
    )

    assert results == [MatchVerdict(id=7, rank=1)]


def test_judge_top_n_uses_agent_runtime_invocation_port(
    run_log: RunLog,
) -> None:
    provider_auth = ProviderAuth(opencode_api_key="operator-key")
    invocation_port = _invocation_port(
        _runtime_result(_judge_output([{"id": 7, "rank": 1}]))
    )
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        provider_auth=provider_auth,
        invocation_port=invocation_port,
    )

    verdicts = extractor.judge_top_n(
        [
            JudgeCandidate(id=7, header="Header 7", summary="Summary 7"),
            JudgeCandidate(id=9, header="Header 9", summary="Summary 9"),
        ]
    )

    assert verdicts == [MatchVerdict(id=7, rank=1)]
    assert invocation_port.calls == [
        {
            "prompt": (
                "v2 [Candidate id=7]\nHeader 7\n\nSummary 7\n\n"
                "[Candidate id=9]\nHeader 9\n\nSummary 9"
            ),
            "logs_root": run_log.logs_dir,
            "call_site": "judge",
            "provider_auth": provider_auth,
        }
    ]


def test_judge_top_n_empty_candidates_returns_empty_list(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(_config(), _prompts(), run_log=run_log)
    results = extractor.judge_top_n([])
    assert results == []
    assert _read_transcripts(run_log, "llm_judge_match") == []
    assert _read_events(run_log, "llm_judge_match") == []


def test_judge_top_n_does_not_write_pipeline_owned_transcript_jsonl(
    run_log: RunLog,
) -> None:
    candidates = _make_candidates(2)
    verdicts_raw = [{"id": c.id, "rank": i + 1} for i, c in enumerate(candidates)]
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(_runtime_result(_judge_output(verdicts_raw))),
    )
    extractor.judge_top_n(candidates)

    transcript_path = run_log.logs_dir / "llm" / "judge_match.transcripts.jsonl"
    assert not transcript_path.exists()


def test_judge_top_n_candidates_appear_in_prompt(
    run_log: RunLog,
) -> None:
    candidates = _make_candidates(2)
    verdicts_raw = [{"id": c.id, "rank": i + 1} for i, c in enumerate(candidates)]
    captured: dict[str, object] = {}
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_capturing_invocation_port(
            _runtime_result(_judge_output(verdicts_raw)),
            captured,
        ),
    )
    extractor.judge_top_n(candidates)
    prompt_sent = str(captured["prompt"])
    assert "[Candidate id=0]" in prompt_sent
    assert "[Candidate id=1]" in prompt_sent
    assert not (run_log.logs_dir / "llm" / "judge_match.transcripts.jsonl").exists()


def test_judge_top_n_coerces_string_id_to_int(
    run_log: RunLog,
) -> None:
    candidates = _make_candidates(3)
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result('<verdicts>[{"id": "0", "rank": 1}]</verdicts>')
        ),
    )
    verdicts = extractor.judge_top_n(candidates)
    assert len(verdicts) == 1
    assert verdicts[0].id == 0
    assert verdicts[0].rank == 1


def test_judge_top_n_rejects_non_numeric_string_verdict_id(
    run_log: RunLog,
) -> None:
    candidates = _make_candidates(3)
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result('<verdicts>[{"id": "abc", "rank": 1}]</verdicts>')
        ),
    )
    with pytest.raises(ExtractorBatchMalformedError):
        extractor.judge_top_n(candidates)


def test_judge_top_n_via_agent_runtime_keeps_candidate_block_shape_and_logs_judge_runtime_file(
    run_log: RunLog,
) -> None:
    captured: dict[str, object] = {}

    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
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
            evidence_dir=runtime_log,
            reset_time=None,
            message=None,
        )

    candidates = [
        JudgeCandidate(
            id=0, header="Title 0\nACME · Hamburg · remote", summary="Summary 0"
        ),
        JudgeCandidate(
            id=1, header="Title 1\nACME · Berlin · remote", summary="Summary 1"
        ),
    ]
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_MockInvocationPort(invoke=_fake_invoke),
    )
    results = extractor.judge_top_n(candidates)

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


def test_judge_top_n_success_logs_verdicts_without_usage_fields(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(_judge_output([{"id": 0, "rank": 1}]))
        ),
    )

    results = extractor.judge_top_n(_make_candidates(1))

    assert results == [MatchVerdict(id=0, rank=1)]
    assert not (run_log.logs_dir / "llm" / "judge_match.transcripts.jsonl").exists()
    event = _read_events(run_log, "llm_judge_match")[-1]
    assert "usage" not in event
    assert "cost_usd" not in event
    assert "duration_s" not in event


def test_judge_top_n_via_agent_runtime_usage_limit_becomes_quota_error(
    run_log: RunLog,
) -> None:
    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        return AgentRuntimeInvocationResult(
            kind="usage_limit",
            output="quota reached",
            evidence_dir=logs_root
            / "llm"
            / "agent-runtime"
            / "judge"
            / "llm-judge-quota.log",
            reset_time=None,
            message=None,
        )

    with pytest.raises(UsageLimitError) as excinfo:
        extractor = AgentRuntimeExtractor(
            _config(),
            _prompts(),
            run_log=run_log,
            invocation_port=_MockInvocationPort(invoke=_fake_invoke),
        )
        extractor.judge_top_n([JudgeCandidate(id=0, header="h", summary="s")])

    assert "usage limit" in str(excinfo.value).lower()


def test_judge_usage_limit_error_uses_agent_runtime_vocabulary(
    run_log: RunLog,
) -> None:
    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        return AgentRuntimeInvocationResult(
            kind="usage_limit",
            output="quota reached",
            evidence_dir=logs_root / "llm-judge-quota.log",
            reset_time=None,
            message=None,
        )

    with pytest.raises(UsageLimitError) as excinfo:
        extractor = AgentRuntimeExtractor(
            _config(),
            _prompts(),
            run_log=run_log,
            invocation_port=_MockInvocationPort(invoke=_fake_invoke),
        )
        extractor.judge_top_n([JudgeCandidate(id=0, header="h", summary="s")])

    message = str(excinfo.value)
    assert "Agent Runtime" in message
    assert "usage limit" in message.lower()


def test_classify_usage_limit_error_uses_agent_runtime_vocabulary(
    run_log: RunLog,
) -> None:
    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        runtime_log = (
            logs_root / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
        )
        runtime_log.parent.mkdir(parents=True, exist_ok=True)
        return AgentRuntimeInvocationResult(
            kind="usage_limit",
            output="limit reached",
            evidence_dir=runtime_log,
            reset_time=datetime(2026, 6, 22, 8, 45, tzinfo=timezone.utc),
            message=None,
        )

    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_MockInvocationPort(invoke=_fake_invoke),
    )

    with pytest.raises(UsageLimitError) as excinfo:
        extractor.classify_relevance([_item()])

    message = str(excinfo.value)
    assert "Agent Runtime" in message
    assert "usage limit" in message.lower()


def test_classify_usage_limit_invocation_result_from_public_llm_import_preserves_reset_time(
    run_log: RunLog,
) -> None:
    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        runtime_log = (
            logs_root / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
        )
        runtime_log.parent.mkdir(parents=True, exist_ok=True)
        return AgentRuntimeInvocationResult(
            kind="usage_limit",
            output="limit reached",
            evidence_dir=runtime_log,
            reset_time=datetime(2026, 6, 22, 8, 45, tzinfo=timezone.utc),
            message=None,
        )

    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_MockInvocationPort(invoke=_fake_invoke),
    )

    with pytest.raises(UsageLimitError) as excinfo:
        extractor.classify_relevance([_item()])

    assert excinfo.value.reset_time == datetime(2026, 6, 22, 8, 45, tzinfo=timezone.utc)
    assert "Agent Runtime usage limit" in str(excinfo.value)


def test_judge_top_n_forwards_provider_auth_to_agent_runtime(
    run_log: RunLog,
) -> None:
    captured: dict[str, object] = {}
    provider_auth = ProviderAuth(opencode_api_key="test-key")

    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        captured["prompt"] = prompt
        captured["call_site"] = call_site
        captured["provider_auth"] = provider_auth
        return AgentRuntimeInvocationResult(
            kind="completed",
            output='<verdicts>[{"id": 0, "rank": 1}]</verdicts>',
            evidence_dir=logs_root / "judge.log",
            reset_time=None,
            message=None,
        )

    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        provider_auth=provider_auth,
        invocation_port=_MockInvocationPort(invoke=_fake_invoke),
    )
    extractor.judge_top_n([JudgeCandidate(id=0, header="h", summary="s")])

    assert captured["call_site"] == "judge"
    assert captured["provider_auth"] is provider_auth


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
) -> None:
    items = [_item(title=f"Job {i + 1}") for i in range(3)]
    output = _batch_classify_output(
        [(1, {"matches": False}), (2, {"matches": False}), (3, {"matches": False})]
    )
    captured: dict[str, object] = {}
    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_capturing_invocation_port(_runtime_result(output), captured),
    )
    extractor.classify_relevance(items)
    prompt_sent = str(captured["prompt"])
    assert "id=1" in prompt_sent
    assert "id=2" in prompt_sent
    assert "id=3" in prompt_sent
    assert not (
        run_log.logs_dir / "llm" / "classify_relevance.transcripts.jsonl"
    ).exists()


def test_classify_relevance_out_of_order_verdicts_map_to_correct_positions(
    run_log: RunLog,
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
    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(_runtime_result(output)),
    )
    results = extractor.classify_relevance(items)
    assert len(results) == 3
    assert results[0] is not None and results[0].header == "h1"
    assert results[1] is not None and results[1].matches is False
    assert results[2] is not None and results[2].header == "h3"


def test_classify_relevance_missing_verdict_tag_produces_none(
    run_log: RunLog,
) -> None:
    items = [_item(title="Job 1"), _item(title="Job 2")]
    # only id=1 present; id=2 missing
    output = _batch_classify_output([(1, {"matches": False})])
    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(_runtime_result(output)),
    )
    results = extractor.classify_relevance(items)
    assert results[0] is not None and results[0].matches is False
    assert results[1] is None


def test_classify_relevance_malformed_verdict_json_produces_none(
    run_log: RunLog,
) -> None:
    items = [_item(title="Job 1"), _item(title="Job 2")]
    output = (
        '<verdict id="1">{"matches": false}</verdict>'
        '<verdict id="2">not valid json</verdict>'
    )
    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(_runtime_result(output)),
    )
    results = extractor.classify_relevance(items)
    assert results[0] is not None and results[0].matches is False
    assert results[1] is None


def test_classify_relevance_all_verdicts_missing_returns_all_none_no_error(
    run_log: RunLog,
) -> None:
    items = [_item(), _item()]
    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(_runtime_result("no verdict tags here")),
    )
    results = extractor.classify_relevance(items)
    assert results == [None, None]


def test_classify_relevance_batch_includes_full_prompt_via_agent_runtime(
    run_log: RunLog,
) -> None:
    items = [_item(title=f"Job {i + 1}") for i in range(3)]
    output = _batch_classify_output(
        [(1, {"matches": False}), (2, {"matches": False}), (3, {"matches": False})]
    )
    captured: dict[str, object] = {}
    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_capturing_invocation_port(_runtime_result(output), captured),
    )
    extractor.classify_relevance(items)
    prompt_sent = str(captured["prompt"])
    assert "Job 1" in prompt_sent
    assert "Job 2" in prompt_sent
    assert "Job 3" in prompt_sent
    assert not (
        run_log.logs_dir / "llm" / "classify_relevance.transcripts.jsonl"
    ).exists()


def test_classify_relevance_success_logs_event_without_usage_fields(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            _runtime_result(_classify_output({"matches": False}))
        ),
    )

    results = extractor.classify_relevance([_item()])

    assert results[0] is not None and results[0].matches is False
    assert not (
        run_log.logs_dir / "llm" / "classify_relevance.transcripts.jsonl"
    ).exists()
    event = _read_events(run_log, "llm_classify_relevance")[-1]
    assert "usage" not in event
    assert "cost_usd" not in event
    assert "duration_s" not in event


def test_classify_relevance_via_agent_runtime_keeps_verdict_shape_and_outcomes(
    run_log: RunLog,
) -> None:
    captured: dict[str, object] = {}

    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
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
            evidence_dir=runtime_log,
            reset_time=None,
            message=None,
        )

    items = [_item(title=f"Job {i + 1}") for i in range(3)]
    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_MockInvocationPort(invoke=_fake_invoke),
    )
    results = extractor.classify_relevance(items)

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


def test_classify_relevance_via_agent_runtime_usage_limit_becomes_quota_error(
    run_log: RunLog,
) -> None:
    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
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
            evidence_dir=runtime_log,
            reset_time=datetime(2026, 6, 22, 8, 45, tzinfo=timezone.utc),
            message=None,
        )

    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_MockInvocationPort(invoke=_fake_invoke),
    )

    with pytest.raises(UsageLimitError) as excinfo:
        extractor.classify_relevance([_item()])

    assert excinfo.value.reset_time == datetime(2026, 6, 22, 8, 45, tzinfo=timezone.utc)


def test_classify_relevance_via_agent_runtime_retryable_failure_marks_items_retryable(
    run_log: RunLog,
) -> None:
    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
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
            evidence_dir=runtime_log,
            reset_time=None,
            message=None,
        )

    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_MockInvocationPort(invoke=_fake_invoke),
    )
    results = extractor.classify_relevance([_item(), _item()])

    assert results == [None, None]


def test_classify_retryable_invocation_result_from_public_llm_port_marks_each_item_retryable(
    run_log: RunLog,
) -> None:
    class _RetryablePort(AgentRuntimeInvocationPort):
        def invoke(
            self,
            prompt: str,
            *,
            logs_root: Path,
            call_site: AgentRuntimeCallSiteName,
            provider_auth: ProviderAuth | None = None,
        ) -> AgentRuntimeInvocationResult:
            runtime_log = (
                logs_root / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
            )
            runtime_log.parent.mkdir(parents=True, exist_ok=True)
            return AgentRuntimeInvocationResult(
                kind="retryable_provider_failure",
                output="provider flake",
                evidence_dir=runtime_log,
                reset_time=None,
                message=None,
            )

    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_RetryablePort(),
    )

    assert extractor.classify_relevance([_item(), _item()]) == [None, None]


def test_classify_relevance_via_agent_runtime_completed_without_usage_stays_valid(
    run_log: RunLog,
) -> None:
    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        assert prompt
        assert call_site == "classify"
        runtime_log = (
            logs_root / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
        )
        runtime_log.parent.mkdir(parents=True, exist_ok=True)
        runtime_log.write_text("missing usage\n", encoding="utf-8")
        return AgentRuntimeInvocationResult(
            kind="completed",
            output=_classify_output({"matches": False}),
            evidence_dir=runtime_log,
            reset_time=None,
            message=None,
        )

    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_MockInvocationPort(invoke=_fake_invoke),
    )

    results = extractor.classify_relevance([_item()])

    assert results == [RelevanceVerdict(matches=False)]


def test_classify_relevance_via_agent_runtime_hard_provider_failure_is_unreachable(
    run_log: RunLog,
) -> None:
    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
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
            evidence_dir=runtime_log,
            reset_time=None,
            message="provider exploded",
        )

    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=_MockInvocationPort(invoke=_fake_invoke),
    )

    with pytest.raises(ExtractorUnreachableError) as excinfo:
        extractor.classify_relevance([_item()])

    assert excinfo.value.stderr == "provider exploded"


def test_classify_hard_provider_failure_from_public_llm_adapter_surfaces_provider_message(
    run_log: RunLog,
) -> None:
    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        runtime_log = (
            logs_root / "llm" / "agent-runtime" / "classify" / "llm-classify.log"
        )
        runtime_log.parent.mkdir(parents=True, exist_ok=True)
        return AgentRuntimeInvocationResult(
            kind="hard_provider_failure",
            output="runtime failed",
            evidence_dir=runtime_log,
            reset_time=None,
            message="provider exploded",
        )

    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        invocation_port=AgentRuntimeInvocationAdapter(invoke=_fake_invoke),
    )

    with pytest.raises(ExtractorUnreachableError) as excinfo:
        extractor.classify_relevance([_item()])

    assert excinfo.value.stderr == "provider exploded"


def test_classify_relevance_forwards_provider_auth_to_agent_runtime(
    run_log: RunLog,
) -> None:
    captured: dict[str, object] = {}
    provider_auth = ProviderAuth(opencode_api_key="test-key")

    def _fake_invoke(
        prompt: str,
        *,
        logs_root: Path,
        call_site: AgentRuntimeCallSiteName,
        provider_auth: ProviderAuth | None = None,
    ) -> AgentRuntimeInvocationResult:
        captured["prompt"] = prompt
        captured["call_site"] = call_site
        captured["provider_auth"] = provider_auth
        return AgentRuntimeInvocationResult(
            kind="completed",
            output='<verdict id="1">{"matches": false}</verdict>',
            evidence_dir=logs_root / "classify.log",
            reset_time=None,
            message=None,
        )

    extractor = AgentRuntimeExtractor(
        _config(),
        _batch_prompts(),
        run_log=run_log,
        provider_auth=provider_auth,
        invocation_port=_MockInvocationPort(invoke=_fake_invoke),
    )
    extractor.classify_relevance([_item()])

    assert captured["call_site"] == "classify"
    assert captured["provider_auth"] is provider_auth


def test_classify_relevance_with_provider_auth_keeps_auth_out_of_ordinary_events(
    run_log: RunLog,
) -> None:
    provider_auth = ProviderAuth(opencode_api_key="test-key")
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        provider_auth=provider_auth,
        invocation_port=_invocation_port(
            _runtime_result(_classify_output({"matches": False}))
        ),
    )

    results = extractor.classify_relevance([_item()])

    assert results == [RelevanceVerdict(matches=False)]
    event = _read_events(run_log, "llm_classify_relevance")[-1]
    assert "provider_auth" not in event
    assert "opencode_api_key" not in json.dumps(event)


def test_judge_top_n_with_provider_auth_keeps_auth_out_of_ordinary_events(
    run_log: RunLog,
) -> None:
    provider_auth = ProviderAuth(opencode_api_key="test-key")
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        provider_auth=provider_auth,
        invocation_port=_invocation_port(
            _runtime_result(_judge_output([{"id": 0, "rank": 1}]))
        ),
    )

    results = extractor.judge_top_n(_make_candidates(1))

    assert results == [MatchVerdict(id=0, rank=1)]
    event = _read_events(run_log, "llm_judge_match")[-1]
    assert "provider_auth" not in event
    assert "opencode_api_key" not in json.dumps(event)


def test_classify_relevance_succeeds_when_runtime_log_file_is_missing(
    run_log: RunLog,
) -> None:
    missing_log = (
        run_log.logs_dir / "llm" / "agent-runtime" / "classify" / "missing.log"
    )
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            AgentRuntimeInvocationResult(
                kind="completed",
                output=_classify_output({"matches": False}),
                evidence_dir=missing_log,
                reset_time=None,
                message=None,
            ),
        ),
    )
    results = extractor.classify_relevance([_item()])
    assert results == [RelevanceVerdict(matches=False)]


def test_classify_relevance_succeeds_when_runtime_evidence_directory_is_missing(
    run_log: RunLog,
) -> None:
    missing_evidence_dir = (
        run_log.logs_dir / "llm" / "agent-runtime" / "classify" / "missing-evidence-dir"
    )
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            AgentRuntimeInvocationResult(
                kind="completed",
                output=_classify_output({"matches": False}),
                evidence_dir=missing_evidence_dir,
                reset_time=None,
                message=None,
            ),
        ),
    )

    results = extractor.classify_relevance([_item()])

    assert results == [RelevanceVerdict(matches=False)]
    assert extractor.last_classify_log_path == missing_evidence_dir
    assert not missing_evidence_dir.exists()


def test_judge_top_n_succeeds_when_runtime_log_file_is_missing(
    run_log: RunLog,
) -> None:
    missing_log = run_log.logs_dir / "llm" / "agent-runtime" / "judge" / "missing.log"
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            AgentRuntimeInvocationResult(
                kind="completed",
                output=_judge_output([{"id": 0, "rank": 1}]),
                evidence_dir=missing_log,
                reset_time=None,
                message=None,
            ),
        ),
    )
    results = extractor.judge_top_n(_make_candidates(1))
    assert results == [MatchVerdict(id=0, rank=1)]


def test_classify_relevance_succeeds_when_runtime_returns_completed_without_usage(
    run_log: RunLog,
) -> None:
    """A completed Agent Runtime classifier response with valid output and no usage
    metadata is applied normally.
    """
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            AgentRuntimeInvocationResult(
                kind="completed",
                output=_classify_output(
                    {
                        "matches": True,
                        "header": "Senior Python Engineer\nAcme · Hamburg · remote\n2024-01-01",
                        "summary": "Great role for ML engineers.",
                    }
                ),
                evidence_dir=Path("llm-classify.log"),
                reset_time=None,
                message=None,
            ),
        ),
    )

    results = extractor.classify_relevance([_item(company="Acme", location="Hamburg")])

    result = results[0]
    assert isinstance(result, RelevanceVerdict)
    assert result.matches is True
    assert result.header is not None
    assert result.summary is not None


def test_judge_top_n_succeeds_when_runtime_returns_completed_without_usage(
    run_log: RunLog,
) -> None:
    extractor = AgentRuntimeExtractor(
        _config(),
        _prompts(),
        run_log=run_log,
        invocation_port=_invocation_port(
            AgentRuntimeInvocationResult(
                kind="completed",
                output=_judge_output([{"id": 0, "rank": 1}]),
                evidence_dir=Path("llm-judge.log"),
                reset_time=None,
                message=None,
            ),
        ),
    )

    results = extractor.judge_top_n(_make_candidates(1))

    assert results == [MatchVerdict(id=0, rank=1)]
