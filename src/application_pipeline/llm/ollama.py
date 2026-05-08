import json
import urllib.request
from typing import Any, Callable, Literal, TypeVar

from application_pipeline.config import Config
from application_pipeline.http import HttpPost, HttpRetryError, post_with_retries
from application_pipeline.prompts import Prompts

from .types import (
    ExtractorUnreachableError,
    LLMExtractorError,
    MatchTier,
    MatchVerdict,
    RelevanceVerdict,
)

_T = TypeVar("_T")

_CLASSIFY_RELEVANCE_FORMAT = {
    "type": "object",
    "properties": {"in_domain": {"type": "boolean"}},
    "required": ["in_domain"],
}

_JUDGE_MATCH_FORMAT = {
    "type": "object",
    "properties": {
        "tier": {"type": "string", "enum": ["green", "amber", "red"]},
        "matched": {"type": "array", "items": {"type": "string"}},
        "missing": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["tier", "matched", "missing", "summary"],
}


def _default_http_post(
    url: str, payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())  # type: ignore[no-any-return]


class OllamaExtractor:
    def __init__(
        self,
        config: Config,
        prompts: Prompts,
        *,
        _http_post: HttpPost | None = None,
    ) -> None:
        self._config = config
        self._prompts = prompts
        self._http_post: HttpPost = _http_post or _default_http_post
        self._skills_block = "\n".join(f"- {s}" for s in config.skills)

    def classify_relevance(
        self, language: str, title: str, raw_description: str
    ) -> RelevanceVerdict:
        lang = self._lang_or_en(language)
        slots = {"title": title, "raw_description": raw_description}
        prompt = self._prompts.classify_relevance[lang].format(**slots)
        payload: dict[str, Any] = {
            "model": self._config.ollama_classify_model,
            "prompt": prompt,
            "format": _CLASSIFY_RELEVANCE_FORMAT,
            "stream": False,
            "keep_alive": self._config.ollama_keep_alive,
        }
        return self._generate_with_retries(
            payload,
            lambda data: RelevanceVerdict(in_domain=bool(data["in_domain"])),
            "classify_relevance",
        )

    def judge_match(self, language: str, raw_description: str) -> MatchVerdict:
        lang = self._lang_or_en(language)
        slots = {"skills": self._skills_block, "raw_description": raw_description}
        prompt = self._prompts.judge_match[lang].format(**slots)
        payload: dict[str, Any] = {
            "model": self._config.ollama_judge_model,
            "prompt": prompt,
            "format": _JUDGE_MATCH_FORMAT,
            "stream": False,
            "keep_alive": self._config.ollama_keep_alive,
            "options": {"temperature": 0.2},
        }
        return self._generate_with_retries(
            payload,
            lambda data: MatchVerdict(
                tier=MatchTier(data["tier"]),
                matched=list(data["matched"]),
                missing=list(data["missing"]),
                summary=str(data["summary"]),
            ),
            "judge_match",
        )

    def prewarm(self) -> None:
        url = f"{self._config.ollama_base_url}/api/generate"
        timeout = float(self._config.ollama_read_timeout_seconds)
        models = [self._config.ollama_classify_model]
        if self._config.ollama_judge_model != self._config.ollama_classify_model:
            models.append(self._config.ollama_judge_model)
        for model in models:
            payload: dict[str, Any] = {
                "model": model,
                "prompt": "ok",
                "options": {"num_predict": 1},
                "keep_alive": self._config.ollama_keep_alive,
            }
            try:
                self._http_post(url, payload, timeout)
            except Exception as exc:
                raise ExtractorUnreachableError(
                    f"Ollama prewarm failed for model {model!r}: {exc}"
                ) from exc

    @staticmethod
    def _lang_or_en(language: str) -> Literal["de", "en"]:
        return "de" if language == "de" else "en"

    def _generate_with_retries(
        self,
        payload: dict[str, Any],
        parser: Callable[[Any], _T],
        method_name: str,
    ) -> _T:
        url = f"{self._config.ollama_base_url}/api/generate"
        timeout = float(self._config.ollama_read_timeout_seconds)
        last_exc: Exception | None = None
        for _ in range(self._config.ollama_json_retries):
            raw = self._call_with_http_retries(url, payload, timeout)
            try:
                return parser(json.loads(raw.get("response", "{}")))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                last_exc = exc
        raise LLMExtractorError(
            f"{method_name}: failed to parse Ollama response: {last_exc}"
        ) from last_exc

    def _call_with_http_retries(
        self, url: str, payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        try:
            return post_with_retries(
                url, payload, timeout, self._config.ollama_http_retries, self._http_post
            )
        except HttpRetryError as exc:
            raise LLMExtractorError(str(exc)) from exc.__cause__
