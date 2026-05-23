import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from application_pipeline.config import Config
from application_pipeline.parser_log import RunLog
from application_pipeline.prompts import Prompts

from .agent_output import AgentOutputProtocolError, extract_json_block
from .claude_cli import (
    ClaudeCliError,
    ClaudeCliInvoker,
    ClaudeMalformedEnvelopeError,
    ClaudeResponse,
)
from .types import (
    CallUsage,
    ClassifyItem,
    ExtractorBatchMalformedError,
    ExtractorError,
    ExtractorMalformedError,
    ExtractorMalformedJSONError,
    ExtractorUnreachableError,
    JudgeCandidate,
    MatchVerdict,
    RelevanceVerdict,
)

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


_CLASSIFY_MODEL = "haiku"
_JUDGE_MODEL = "haiku"
_JUDGE_EFFORT = "medium"


@dataclass(frozen=True)
class _CallSite:
    call: str
    component_id: str
    tag: str
    model: str
    effort: str
    protocol_error_cls: type[ExtractorError]


_CLASSIFY_SITE = _CallSite(
    call="classify_relevance",
    component_id="llm_classify_relevance",
    tag="verdict",
    model=_CLASSIFY_MODEL,
    effort="",
    protocol_error_cls=ExtractorMalformedError,
)

_JUDGE_TOP_N_SITE = _CallSite(
    call="judge_top_n",
    component_id="llm_judge_match",
    tag="verdicts",
    model=_JUDGE_MODEL,
    effort=_JUDGE_EFFORT,
    protocol_error_cls=ExtractorBatchMalformedError,
)


def _build_listing_bullets(item: ClassifyItem) -> str:
    lines = [f"- Jobtitel: {item.title}"]
    if item.company and item.company.strip():
        lines.append(f"- Unternehmen: {item.company}")
    if item.location and item.location.strip():
        lines.append(f"- Ort: {item.location}")
    if item.posted_date is not None:
        lines.append(f"- Listing-Datum: {item.posted_date}")
    return "\n".join(lines)


class ClaudeExtractor:
    def __init__(
        self,
        config: Config,
        prompts: Prompts,
        *,
        run_log: RunLog,
        _invoker: ClaudeCliInvoker | None = None,
    ) -> None:
        self._config = config
        self._prompts = prompts
        self._run_log = run_log
        self._invoker = _invoker or ClaudeCliInvoker(cli_path=config.claude_cli_path)

    def classify_relevance(
        self, item: ClassifyItem
    ) -> tuple[RelevanceVerdict, CallUsage]:
        prompt = self._prompts.classify_relevance.render(
            LISTING_BULLETS=_build_listing_bullets(item),
            RAW_DESCRIPTION=item.raw_description,
        )
        parsed, response = self._invoke(_CLASSIFY_SITE, prompt, {})
        usage = self._usage_from(response)
        return self._parse_relevance(parsed), usage

    def judge_top_n(
        self, candidates: list[JudgeCandidate]
    ) -> tuple[list[MatchVerdict], CallUsage]:
        if not candidates:
            return [], CallUsage(
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cost_usd=0.0,
                duration_s=0.0,
            )
        candidates_block = self._format_candidates(candidates)
        prompt = self._prompts.judge_top_n.render(CANDIDATES=candidates_block)
        data, response = self._invoke(
            _JUDGE_TOP_N_SITE, prompt, {"candidate_count": len(candidates)}
        )
        usage = self._usage_from(response)
        return self._parse_top_n_response(data, candidates), usage

    def _invoke(
        self,
        site: _CallSite,
        prompt: str,
        extra: dict[str, object],
    ) -> tuple[Any, ClaudeResponse]:
        t0 = time.monotonic()
        try:
            response = self._invoker.call(
                prompt,
                model=site.model,
                effort=site.effort,
            )
        except (ClaudeCliError, ClaudeMalformedEnvelopeError) as exc:
            status = (
                "cli_error" if isinstance(exc, ClaudeCliError) else "malformed_envelope"
            )
            self._write_transcript(
                site=site,
                prompt=prompt,
                status=status,
                duration_s=time.monotonic() - t0,
                extra=extra,
                exc=exc,
            )
            err_cls = (
                ExtractorUnreachableError
                if isinstance(exc, ClaudeCliError)
                else ExtractorMalformedJSONError
            )
            raise err_cls(
                str(exc), returncode=exc.returncode, stderr=exc.stderr
            ) from exc
        # ClaudeUsageLimitError propagates as-is for abort handling

        try:
            parsed = extract_json_block(response.raw_response, site.tag)
        except AgentOutputProtocolError as exc:
            self._write_transcript(
                site=site,
                prompt=prompt,
                status="protocol_error",
                duration_s=time.monotonic() - t0,
                extra=extra,
                raw_response=response.raw_response,
                kind=exc.kind,
            )
            raise site.protocol_error_cls(
                f"{site.call}: {exc.kind}: <{site.tag}> block missing or malformed"
            ) from exc

        transcript_entry: dict[str, object] = {
            "call": site.call,
            "prompt": prompt,
            "raw_response": response.raw_response,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_tokens": response.usage.cache_read_tokens,
            },
            "cost_usd": response.cost_usd,
            "duration_s": response.duration_s,
        }
        self._run_log.transcript(site.component_id, transcript_entry)
        self._run_log.event(
            site.component_id,
            site.call,
            cost_usd=response.cost_usd,
            duration_s=f"{response.duration_s:.3f}",
        )

        return parsed, response

    def _write_transcript(
        self,
        *,
        site: _CallSite,
        prompt: str,
        status: str,
        duration_s: float,
        extra: dict[str, object],
        exc: ClaudeCliError | ClaudeMalformedEnvelopeError | None = None,
        raw_response: str | None = None,
        kind: str | None = None,
    ) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry: dict[str, object] = {
            "ts": ts,
            "call": site.call,
            "status": status,
            "prompt": prompt,
            "duration_s": duration_s,
            **extra,
        }
        if exc is not None:
            entry["stdout"] = exc.stdout
            entry["stderr"] = exc.stderr
            entry["returncode"] = exc.returncode
            entry["envelope"] = exc.envelope
            entry["envelope_error_class"] = exc.envelope_error_class
        if raw_response is not None:
            entry["raw_response"] = raw_response
        if kind is not None:
            entry["envelope_error_class"] = kind
        self._run_log.transcript(site.component_id, entry)
        self._run_log.event(
            site.component_id,
            site.call,
            status=status,
            duration_s=f"{duration_s:.3f}",
        )

    @staticmethod
    def _usage_from(response: ClaudeResponse) -> CallUsage:
        return CallUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=response.usage.cache_read_tokens,
            cost_usd=response.cost_usd,
            duration_s=response.duration_s,
        )

    @staticmethod
    def _format_candidates(candidates: list[JudgeCandidate]) -> str:
        parts: list[str] = []
        for c in candidates:
            parts.append(f"[Candidate id={c.id}]\n{c.header}\n\n{c.summary}")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_relevance(parsed_result: object) -> RelevanceVerdict:
        if not isinstance(parsed_result, dict):
            raise ExtractorMalformedError(
                f"classify_relevance: expected JSON object, got {type(parsed_result).__name__}"
            )
        matches = parsed_result.get("matches")
        if not isinstance(matches, bool):
            raise ExtractorMalformedError(
                f"classify_relevance: matches must be bool, got {matches!r}"
            )
        if not matches:
            return RelevanceVerdict(matches=False)
        header = parsed_result.get("header")
        summary = parsed_result.get("summary")
        if not isinstance(header, str) or not header:
            raise ExtractorMalformedError(
                "classify_relevance: header must be a non-empty string for matching verdict"
            )
        if not isinstance(summary, str) or not summary:
            raise ExtractorMalformedError(
                "classify_relevance: summary must be a non-empty string for matching verdict"
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
        seen_ids: set[str] = set()
        verdicts: list[MatchVerdict] = []
        for entry in data:
            if not isinstance(entry, dict):
                raise ExtractorBatchMalformedError(
                    f"judge_top_n: verdict entry is not a dict: {entry!r}"
                )
            entry_id = entry.get("id")
            if not isinstance(entry_id, str) or entry_id not in valid_ids:
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
