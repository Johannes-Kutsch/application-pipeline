import pathlib
from dataclasses import dataclass

from .config import Config, ConfigError


@dataclass(frozen=True)
class Prompts:
    classify_relevance: str
    judge_match: str


def load_prompts(config: Config) -> Prompts:
    return Prompts(
        classify_relevance=_read(
            "CLASSIFY_RELEVANCE_PROMPT", config.classify_relevance_prompt
        ),
        judge_match=_read("JUDGE_MATCH_PROMPT", config.judge_match_prompt),
    )


def _read(name: str, path: pathlib.Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{name} could not be read from {path}: {exc}") from exc
    if not text.strip():
        raise ConfigError(f"{name} file is empty: {path}")
    return text
