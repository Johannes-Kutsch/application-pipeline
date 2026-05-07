import json
import urllib.request
from typing import Any, Callable, Literal

from application_pipeline.config import Config
from application_pipeline.prompts import Prompts

from .types import LLMExtractorError, MatchVerdict, RelevanceVerdict

_HttpPost = Callable[[str, dict[str, Any], float], dict[str, Any]]

_CLASSIFY_RELEVANCE_FORMAT = {
    "type": "object",
    "properties": {"in_domain": {"type": "boolean"}},
    "required": ["in_domain"],
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
        _http_post: _HttpPost | None = None,
    ) -> None:
        self._config = config
        self._prompts = prompts
        self._http_post: _HttpPost = _http_post or _default_http_post

    def classify_relevance(
        self, language: str, title: str, raw_description: str
    ) -> RelevanceVerdict:
        lang: Literal["de", "en"] = "de" if language == "de" else "en"
        prompt = self._prompts.classify_relevance[lang].format(
            title=title, raw_description=raw_description
        )
        payload: dict[str, Any] = {
            "model": self._config.ollama_classify_model,
            "prompt": prompt,
            "format": _CLASSIFY_RELEVANCE_FORMAT,
            "stream": False,
            "keep_alive": self._config.ollama_keep_alive,
        }
        url = f"{self._config.ollama_base_url}/api/generate"
        timeout = float(self._config.ollama_read_timeout_seconds)

        last_exc: Exception | None = None
        for _ in range(self._config.ollama_json_retries):
            raw = self._call_with_http_retries(url, payload, timeout)
            try:
                data = json.loads(raw.get("response", "{}"))
                return RelevanceVerdict(in_domain=bool(data["in_domain"]))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                last_exc = exc
        raise LLMExtractorError(
            f"classify_relevance: failed to parse Ollama response: {last_exc}"
        ) from last_exc

    def judge_match(self, language: str, raw_description: str) -> MatchVerdict:
        raise NotImplementedError

    def _call_with_http_retries(
        self, url: str, payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for _ in range(self._config.ollama_http_retries):
            try:
                return self._http_post(url, payload, timeout)
            except Exception as exc:
                last_exc = exc
        raise LLMExtractorError(
            f"Ollama HTTP request failed after retries: {last_exc}"
        ) from last_exc
