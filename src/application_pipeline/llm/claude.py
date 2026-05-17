import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from application_pipeline import parser_log
from application_pipeline.config import Config
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
    ExtractorMalformedJSONError,
    ExtractorSchemaError,
    ExtractorUnreachableError,
    MatchTier,
    MatchVerdict,
    RelevanceVerdict,
)

_COMPONENT_ID = "claude_extractor"

_CLASSIFY_MODEL = "haiku"
_JUDGE_MODEL = "sonnet"
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
    call="classify_relevance_batch",
    component_id="classify_relevance",
    tag="verdicts",
    model=_CLASSIFY_MODEL,
    effort="",
    protocol_error_cls=ExtractorBatchMalformedError,
)

_JUDGE_SITE = _CallSite(
    call="judge_match",
    component_id="judge_match",
    tag="verdict",
    model=_JUDGE_MODEL,
    effort=_JUDGE_EFFORT,
    protocol_error_cls=ExtractorMalformedJSONError,
)


class ClaudeExtractor:
    def __init__(
        self,
        config: Config,
        prompts: Prompts,
        *,
        _invoker: ClaudeCliInvoker | None = None,
    ) -> None:
        self._config = config
        self._prompts = prompts
        self._invoker = _invoker or ClaudeCliInvoker(cli_path=config.claude_cli_path)
        self._skills_block = "\n".join(f"- {s}" for s in config.skills)

    def classify_relevance_batch(
        self, items: list[ClassifyItem]
    ) -> tuple[list[RelevanceVerdict], CallUsage]:
        items_block = self._format_classify_items(items)
        prompt = self._prompts.classify_relevance.render(ITEMS=items_block)
        parsed, response = self._invoke(
            _CLASSIFY_SITE,
            prompt,
            {"item_ids": [item.id for item in items]},
            batch_size=len(items),
        )
        usage = self._usage_from(response)
        return self._parse_batch_response(parsed, items), usage

    def judge_match(
        self, raw_description: str, *, stub_url: str = ""
    ) -> tuple[MatchVerdict, CallUsage]:
        prompt = self._prompts.judge_match.render(
            skills=self._skills_block, raw_description=raw_description
        )
        data, response = self._invoke(_JUDGE_SITE, prompt, {"stub_url": stub_url})
        usage = self._usage_from(response)
        try:
            return (
                MatchVerdict(
                    tier=MatchTier(data["tier"]),
                    matched=list(data["matched"])[:10],
                    missing=list(data["missing"])[:10],
                    summary=str(data["summary"]),
                ),
                usage,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ExtractorSchemaError(
                f"judge_match: failed to validate Claude response: {exc}"
            ) from exc

    def _invoke(
        self,
        site: _CallSite,
        prompt: str,
        extra: dict[str, object],
        *,
        batch_size: int | None = None,
    ) -> tuple[Any, ClaudeResponse]:
        t0 = time.monotonic()
        try:
            response = self._invoker.call(prompt, model=site.model, effort=site.effort)
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

        transcript: dict[str, object] = {
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
        if batch_size is not None:
            transcript["batch_size"] = batch_size
        parser_log.record_transcript(_COMPONENT_ID, transcript)

        record_kwargs: dict[str, object] = {
            "cost_usd": response.cost_usd,
            "duration_s": f"{response.duration_s:.3f}",
        }
        if batch_size is not None:
            record_kwargs["batch_size"] = batch_size
        parser_log.record(_COMPONENT_ID, site.call, **record_kwargs)

        return parsed, response

    @staticmethod
    def _write_transcript(
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
        parser_log.record_transcript(site.component_id, entry)

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
    def _format_classify_items(items: list[ClassifyItem]) -> str:
        parts: list[str] = []
        for item in items:
            parts.append(
                f"[Item id={item.id}]\nTitle: {item.title}\nDescription: {item.raw_description}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _parse_batch_response(
        parsed_result: object, items: list[ClassifyItem]
    ) -> list[RelevanceVerdict]:
        if not isinstance(parsed_result, list):
            raise ExtractorBatchMalformedError(
                f"classify_relevance_batch: expected JSON array, got {type(parsed_result).__name__}"
            )
        if len(parsed_result) != len(items):
            raise ExtractorBatchMalformedError(
                f"classify_relevance_batch: length mismatch — "
                f"sent {len(items)} items, got {len(parsed_result)} verdicts"
            )

        input_ids = [item.id for item in items]
        response_by_id: dict[str, bool] = {}
        for entry in parsed_result:
            if not isinstance(entry, dict):
                raise ExtractorBatchMalformedError(
                    f"classify_relevance_batch: verdict entry is not a dict: {entry!r}"
                )
            entry_id = entry.get("id")
            if entry_id is None or entry_id not in input_ids:
                raise ExtractorBatchMalformedError(
                    f"classify_relevance_batch: unknown or missing id in verdict: {entry_id!r}"
                )
            if entry_id in response_by_id:
                raise ExtractorBatchMalformedError(
                    f"classify_relevance_batch: duplicate id in response: {entry_id!r}"
                )
            in_domain = entry.get("in_domain")
            if not isinstance(in_domain, bool):
                raise ExtractorBatchMalformedError(
                    f"classify_relevance_batch: in_domain must be bool for id {entry_id!r}"
                )
            response_by_id[entry_id] = in_domain

        return [RelevanceVerdict(in_domain=response_by_id[item.id]) for item in items]
