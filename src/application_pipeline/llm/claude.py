from typing import Literal

from application_pipeline import parser_log
from application_pipeline.config import Config
from application_pipeline.prompts import Prompts

from .claude_cli import (
    ClaudeCliError,
    ClaudeCliInvoker,
    ClaudeMalformedEnvelopeError,
)
from .types import (
    ClassifyItem,
    ExtractorBatchMalformedError,
    ExtractorMalformedJSONError,
    ExtractorSchemaError,
    ExtractorUnreachableError,
    MatchTier,
    MatchVerdict,
    RelevanceVerdict,
)

_COMPONENT_ID = "claude_extractor"


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
        self, language: str, items: list[ClassifyItem]
    ) -> list[RelevanceVerdict]:
        lang = self._lang_or_en(language)
        items_block = self._format_classify_items(items)
        prompt = self._prompts.classify_relevance[lang].render(ITEMS=items_block)
        try:
            response = self._invoker.call(prompt, language)
        except ClaudeCliError as exc:
            raise ExtractorUnreachableError(str(exc)) from exc
        except ClaudeMalformedEnvelopeError as exc:
            raise ExtractorMalformedJSONError(str(exc)) from exc
        # ClaudeUsageLimitError propagates as-is for abort handling

        parser_log.record_transcript(
            _COMPONENT_ID,
            {
                "call": "classify_relevance_batch",
                "language": language,
                "batch_size": len(items),
                "prompt": prompt,
                "raw_response": response.raw_response,
                "parsed_result": response.parsed_result,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cache_read_tokens": response.usage.cache_read_tokens,
                },
                "cost_usd": response.cost_usd,
                "duration_s": response.duration_s,
            },
        )
        parser_log.record_event(
            _COMPONENT_ID,
            "classify_relevance_batch",
            language=language,
            batch_size=len(items),
            cost_usd=response.cost_usd,
            duration_s=f"{response.duration_s:.3f}",
        )

        return self._parse_batch_response(response.parsed_result, items)

    def judge_match(self, language: str, raw_description: str) -> MatchVerdict:
        lang = self._lang_or_en(language)
        prompt = self._prompts.judge_match[lang].render(
            skills=self._skills_block, raw_description=raw_description
        )
        try:
            response = self._invoker.call(prompt, language)
        except ClaudeCliError as exc:
            raise ExtractorUnreachableError(str(exc)) from exc
        except ClaudeMalformedEnvelopeError as exc:
            raise ExtractorMalformedJSONError(str(exc)) from exc
        # ClaudeUsageLimitError propagates as-is for abort handling

        parser_log.record_transcript(
            _COMPONENT_ID,
            {
                "call": "judge_match",
                "language": language,
                "prompt": prompt,
                "raw_response": response.raw_response,
                "parsed_result": response.parsed_result,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cache_read_tokens": response.usage.cache_read_tokens,
                },
                "cost_usd": response.cost_usd,
                "duration_s": response.duration_s,
            },
        )
        parser_log.record_event(
            _COMPONENT_ID,
            "judge_match",
            language=language,
            cost_usd=response.cost_usd,
            duration_s=f"{response.duration_s:.3f}",
        )

        data = response.parsed_result
        try:
            return MatchVerdict(
                tier=MatchTier(data["tier"]),
                matched=list(data["matched"]),
                missing=list(data["missing"]),
                summary=str(data["summary"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ExtractorSchemaError(
                f"judge_match: failed to validate Claude response: {exc}"
            ) from exc

    def prewarm(self) -> None:
        pass  # Claude CLI is a stateless executable; no warm-up needed

    @staticmethod
    def _lang_or_en(language: str) -> Literal["de", "en"]:
        return "de" if language == "de" else "en"

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

        extra_ids = set(response_by_id) - set(input_ids)
        if extra_ids:
            raise ExtractorBatchMalformedError(
                f"classify_relevance_batch: extra ids in response: {extra_ids!r}"
            )

        try:
            return [
                RelevanceVerdict(in_domain=response_by_id[item.id]) for item in items
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ExtractorBatchMalformedError(
                f"classify_relevance_batch: failed to build verdicts: {exc}"
            ) from exc
