import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from application_pipeline.config import Config
from application_pipeline.parser_log import RunLog
from application_pipeline.prompts import Prompts

from agent_runtime.runtime import ProviderAuth

from .agent_output import (
    AgentOutputProtocolError,
    extract_id_tagged_verdicts,
    extract_json_block,
)
from .agent_runtime_invocation import invoke_agent_runtime
from .agent_runtime_types import (
    AgentRuntimeResponse,
    UsageLimitError,
)
from .types import (
    ClassifyItem,
    ExtractorBatchMalformedError,
    ExtractorError,
    ExtractorMalformedError,
    ExtractorUnreachableError,
    JudgeCandidate,
    MatchVerdict,
    RelevanceVerdict,
)


class _RetryableProviderFailureError(Exception):
    def __init__(self) -> None:
        super().__init__("classify_relevance: retryable provider failure")


_GERMAN_BOILERPLATE_SENTINELS: list[str] = [
    "wir bieten",
    "was wir bieten",
    "über uns",
    "über das unternehmen",
    "unser angebot",
    "benefits",
    "das bieten wir",
    "wir als arbeitgeber",
    "das erwartet dich bei uns",
    "das erwartet sie bei uns",
    "bewerben sie sich",
    "bewerbung richten",
    "senden sie ihre bewerbung",
    "schicken sie ihre bewerbung",
    "ihre bewerbung",
    "bewerbungsschluss",
    "bewerbungsfrist",
    "wir freuen uns auf ihre bewerbung",
    "wir freuen uns auf deine bewerbung",
]


def _strip_boilerplate(text: str) -> str:
    paragraphs = text.split("\n\n")
    result: list[str] = []
    for paragraph in paragraphs:
        normalized = paragraph.strip().lower()
        if any(
            normalized.startswith(sentinel)
            for sentinel in _GERMAN_BOILERPLATE_SENTINELS
        ):
            break
        result.append(paragraph)
    return "\n\n".join(result).rstrip()


@dataclass(frozen=True)
class _CallSite:
    call: str
    component_id: str
    tag: str
    protocol_error_cls: type[ExtractorError]


_CLASSIFY_SITE = _CallSite(
    call="classify_relevance",
    component_id="llm_classify_relevance",
    tag="verdict",
    protocol_error_cls=ExtractorMalformedError,
)

_JUDGE_TOP_N_SITE = _CallSite(
    call="judge_top_n",
    component_id="llm_judge_match",
    tag="verdicts",
    protocol_error_cls=ExtractorBatchMalformedError,
)


def _build_item_bullets(item: ClassifyItem) -> str:
    lines = [f"- Jobtitel: {item.title}"]
    if item.company and item.company.strip():
        lines.append(f"- Unternehmen: {item.company}")
    if item.location and item.location.strip():
        lines.append(f"- Ort: {item.location}")
    if item.posted_date is not None:
        lines.append(f"- Listing-Datum: {item.posted_date}")
    return "\n".join(lines)


def _build_listings_block(items: list[ClassifyItem]) -> str:
    parts: list[str] = []
    for i, item in enumerate(items):
        item_id = i + 1
        bullets = _build_item_bullets(item)
        parts.append(
            f"## Stellenanzeige id={item_id}\n\n{bullets}\n\n{item.raw_description}"
        )
    return "\n\n".join(parts)


class AgentRuntimeExtractor:
    def __init__(
        self,
        config: Config,
        prompts: Prompts,
        *,
        run_log: RunLog,
        provider_auth: ProviderAuth | None = None,
    ) -> None:
        self._config = config
        self._prompts = prompts
        self._run_log = run_log
        self._provider_auth = provider_auth
        self._local = threading.local()

    @property
    def last_classify_log_path(self) -> Path | None:
        return getattr(self._local, "last_classify_log_path", None)

    def classify_relevance(
        self, items: list[ClassifyItem]
    ) -> list[RelevanceVerdict | None]:
        if not items:
            return []
        prompt = self._prompts.classify_relevance.render(
            LISTINGS=_build_listings_block(items)
        )
        try:
            response = self._invoke_runtime(prompt)
        except _RetryableProviderFailureError:
            return [None for _ in items]

        verdicts_by_id = extract_id_tagged_verdicts(response.raw_response)
        results: list[RelevanceVerdict | None] = []
        for i in range(len(items)):
            item_id = i + 1
            raw = verdicts_by_id.get(item_id)
            if raw is None:
                results.append(None)
            else:
                try:
                    results.append(
                        self._parse_relevance(
                            raw,
                            prompt=prompt,
                            raw_response=response.raw_response,
                        )
                    )
                except ExtractorMalformedError:
                    results.append(None)

        self._run_log.event(
            _CLASSIFY_SITE.component_id,
            _CLASSIFY_SITE.call,
        )
        return results

    def judge_top_n(self, candidates: list[JudgeCandidate]) -> list[MatchVerdict]:
        if not candidates:
            return []
        candidates_block = self._format_candidates(candidates)
        prompt = self._prompts.judge_top_n.render(CANDIDATES=candidates_block)
        data, response = self._invoke_runtime_protocol(
            _JUDGE_TOP_N_SITE,
            prompt,
        )
        return self._parse_top_n_response(data, candidates)

    def _invoke_runtime_protocol(
        self,
        site: _CallSite,
        prompt: str,
    ) -> tuple[Any, AgentRuntimeResponse]:
        t0 = time.monotonic()
        result = invoke_agent_runtime(
            prompt,
            logs_root=self._run_log.logs_dir,
            call_site="judge",
            provider_auth=self._provider_auth,
        )
        if result.kind == "completed":
            response = AgentRuntimeResponse(
                raw_response=result.output,
            )
            try:
                parsed, is_fallback = extract_json_block(
                    response.raw_response, site.tag
                )
            except AgentOutputProtocolError as exc:
                self._run_log.event(
                    site.component_id,
                    site.call,
                    status="protocol_error",
                    duration_s=f"{time.monotonic() - t0:.3f}",
                )
                msg = (
                    f"{site.call}: {exc.kind}: <{site.tag}> block missing or malformed"
                )
                if site.protocol_error_cls is ExtractorMalformedError:
                    raise ExtractorMalformedError(
                        msg, prompt=prompt, raw_response=response.raw_response
                    ) from exc
                raise site.protocol_error_cls(msg) from exc

            if is_fallback:
                self._run_log.event(
                    site.component_id,
                    site.call,
                    status="protocol_fallback",
                    duration_s=f"{time.monotonic() - t0:.3f}",
                )
                return parsed, response

            self._run_log.event(
                site.component_id,
                site.call,
            )

            return parsed, response
        if result.kind == "usage_limit":
            raise UsageLimitError(
                f"{site.call}: Agent Runtime usage limit reached",
                returncode=0,
                stdout=result.output,
                stderr=result.output,
                envelope={"result": result.output},
                reset_time=result.reset_time,
            )
        raise ExtractorUnreachableError(
            f"{site.call}: Agent Runtime provider failure",
            returncode=0,
            stderr=result.message or result.output,
        )

    @staticmethod
    def _format_candidates(candidates: list[JudgeCandidate]) -> str:
        parts: list[str] = []
        for c in candidates:
            parts.append(f"[Candidate id={c.id}]\n{c.header}\n\n{c.summary}")
        return "\n\n".join(parts)

    def _invoke_runtime(self, prompt: str) -> AgentRuntimeResponse:
        result = invoke_agent_runtime(
            prompt,
            logs_root=self._run_log.logs_dir,
            call_site="classify",
            provider_auth=self._provider_auth,
        )
        self._local.last_classify_log_path = result.evidence_dir
        if result.kind == "completed":
            return AgentRuntimeResponse(
                raw_response=result.output,
            )
        if result.kind == "usage_limit":
            raise UsageLimitError(
                "classify_relevance: Agent Runtime usage limit reached",
                returncode=0,
                stdout=result.output,
                stderr=result.output,
                envelope={"result": result.output},
                reset_time=result.reset_time,
            )
        if result.kind == "retryable_provider_failure":
            raise _RetryableProviderFailureError()
        raise ExtractorUnreachableError(
            "classify_relevance: Agent Runtime provider failure",
            returncode=0,
            stderr=result.message or result.output,
        )

    @staticmethod
    def _parse_relevance(
        parsed_result: object, *, prompt: str, raw_response: str
    ) -> RelevanceVerdict:
        if not isinstance(parsed_result, dict):
            raise ExtractorMalformedError(
                f"classify_relevance: expected JSON object, got {type(parsed_result).__name__}",
                prompt=prompt,
                raw_response=raw_response,
            )
        matches = parsed_result.get("matches")
        if not isinstance(matches, bool):
            raise ExtractorMalformedError(
                f"classify_relevance: matches must be bool, got {matches!r}",
                prompt=prompt,
                raw_response=raw_response,
            )
        if not matches:
            return RelevanceVerdict(matches=False)
        header = parsed_result.get("header")
        summary = parsed_result.get("summary")
        if not isinstance(header, str) or not header:
            raise ExtractorMalformedError(
                "classify_relevance: header must be a non-empty string for matching verdict",
                prompt=prompt,
                raw_response=raw_response,
            )
        if not isinstance(summary, str) or not summary:
            raise ExtractorMalformedError(
                "classify_relevance: summary must be a non-empty string for matching verdict",
                prompt=prompt,
                raw_response=raw_response,
            )
        return RelevanceVerdict(matches=True, header=header, summary=summary)

    @staticmethod
    def _parse_top_n_response(
        data: object, candidates: list[JudgeCandidate]
    ) -> list[MatchVerdict]:
        if not isinstance(data, list):
            raise ExtractorBatchMalformedError(
                f"judge_top_n: expected JSON array, got {type(data).__name__}"
            )
        if len(data) > 5:
            raise ExtractorBatchMalformedError(
                f"judge_top_n: response contains {len(data)} verdicts, expected at most 5"
            )
        valid_ids = {c.id for c in candidates}
        seen_ranks: set[int] = set()
        seen_ids: set[int] = set()
        verdicts: list[MatchVerdict] = []
        for entry in data:
            if not isinstance(entry, dict):
                raise ExtractorBatchMalformedError(
                    f"judge_top_n: verdict entry is not a dict: {entry!r}"
                )
            entry_id = entry.get("id")
            if isinstance(entry_id, str):
                try:
                    entry_id = int(entry_id)
                except (ValueError, TypeError):
                    pass
            if not isinstance(entry_id, int) or entry_id not in valid_ids:
                raise ExtractorBatchMalformedError(
                    f"judge_top_n: unknown or missing id in verdict: {entry_id!r}"
                )
            if entry_id in seen_ids:
                raise ExtractorBatchMalformedError(
                    f"judge_top_n: duplicate id in response: {entry_id!r}"
                )
            rank = entry.get("rank")
            if not isinstance(rank, int) or not (1 <= rank <= 5):
                raise ExtractorBatchMalformedError(
                    f"judge_top_n: rank must be int in 1..5, got {rank!r} for id {entry_id!r}"
                )
            if rank in seen_ranks:
                raise ExtractorBatchMalformedError(
                    f"judge_top_n: duplicate rank {rank} in response"
                )
            seen_ranks.add(rank)
            seen_ids.add(entry_id)
            verdicts.append(MatchVerdict(id=entry_id, rank=rank))
        return verdicts
