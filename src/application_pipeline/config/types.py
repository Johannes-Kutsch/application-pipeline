import pathlib
from dataclasses import dataclass


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class SourceEntry:
    parser_type: str
    max_results: int = 1000


@dataclass(frozen=True)
class Config:
    keywords: list[str]
    skills: list[str]
    sources: list[SourceEntry]
    locations: list[str]
    include_remote: bool = False
    relevance_prompt_path: pathlib.Path | None = None
    match_prompt_path: pathlib.Path | None = None
