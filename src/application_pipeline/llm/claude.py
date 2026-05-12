from typing import Literal

from application_pipeline import parser_log
from application_pipeline.config import Config
from application_pipeline.prompts import Prompts

from .claude_cli import (
    ClaudeCliError,
    ClaudeCliInvoker,
    ClaudeMalformedEnvelopeError,
    ClaudeUsageLimitError,
)
from .types import (
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

    def classify_relevance(
        self, language: str, title: str, raw_description: str
    ) -> RelevanceVerdict:
        lang = self._lang_or_en(language)
        prompt = self._prompts.classify_relevance[lang].render(
            title=title, raw_description=raw_description
        )
        try:
            response = self._invoker.call(prompt, language)
        except (ClaudeCliError, ClaudeUsageLimitError) as exc:
            raise ExtractorUnreachableError(str(exc)) from exc
        except ClaudeMalformedEnvelopeError as exc:
            raise ExtractorMalformedJSONError(str(exc)) from exc

        parser_log.record_transcript(
            _COMPONENT_ID,
            {
                "call": "classify_relevance",
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
            "classify_relevance",
            language=language,
            cost_usd=response.cost_usd,
            duration_s=f"{response.duration_s:.3f}",
        )

        try:
            return RelevanceVerdict(in_domain=response.parsed_result["in_domain"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExtractorSchemaError(
                f"classify_relevance: failed to validate Claude response: {exc}"
            ) from exc

    def judge_match(self, language: str, raw_description: str) -> MatchVerdict:
        lang = self._lang_or_en(language)
        prompt = self._prompts.judge_match[lang].render(
            skills=self._skills_block, raw_description=raw_description
        )
        try:
            response = self._invoker.call(prompt, language)
        except (ClaudeCliError, ClaudeUsageLimitError) as exc:
            raise ExtractorUnreachableError(str(exc)) from exc
        except ClaudeMalformedEnvelopeError as exc:
            raise ExtractorMalformedJSONError(str(exc)) from exc

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
