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
    include_remote: bool = True
    inclusion_keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    seen_store_path: pathlib.Path = field(
        default_factory=lambda: pathlib.Path(".seen.json")
    )
    layout: pathlib.Path | None = None
    prompts_dir: pathlib.Path = field(default_factory=lambda: pathlib.Path("prompts"))
    classify_relevance_prompt: pathlib.Path | None = None
    judge_match_prompt: pathlib.Path | None = None
    claude_cli_path: str | None = None
    claude_classify_batch_size: int = 100
