import pathlib
from dataclasses import dataclass, field

from application_pipeline.user_settings import UserSettingsError


class ConfigError(UserSettingsError):
    pass


@dataclass(frozen=True)
class SourceEntry:
    parser_type: str
    max_results: int = 1000

    def __post_init__(self) -> None:
        if not isinstance(self.parser_type, str) or not self.parser_type.strip():
            raise ConfigError("parser_type must be a non-empty, non-whitespace string")
        if (
            isinstance(self.max_results, bool)
            or not isinstance(self.max_results, int)
            or self.max_results <= 0
        ):
            raise ConfigError("max_results must be a positive integer")


@dataclass(frozen=True)
class Config:
    keywords: list[str]
    skills: list[str]
    sources: list[SourceEntry]
    locations: list[str]
    include_remote: bool = False
    inclusion_keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    prompts_dir: pathlib.Path = field(default_factory=lambda: pathlib.Path("prompts"))
    ollama_base_url: str = "http://localhost:11434"
    ollama_classify_model: str = "qwen3:8b"
    ollama_judge_model: str = "qwen3:8b"
    ollama_read_timeout_seconds: int = 120
    ollama_json_retries: int = 3
    ollama_http_retries: int = 3
    ollama_keep_alive: str = "5m"
