import pathlib
from dataclasses import dataclass, field


class ConfigError(Exception):
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
    classify_relevance_prompt: pathlib.Path = field(
        default_factory=lambda: pathlib.Path("prompts/classify_relevance.md")
    )
    judge_match_prompt: pathlib.Path = field(
        default_factory=lambda: pathlib.Path("prompts/judge_match.md")
    )
