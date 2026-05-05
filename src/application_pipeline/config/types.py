from dataclasses import dataclass


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class SourceEntry:
    parser_type: str
    max_results: int


@dataclass(frozen=True)
class Config:
    keywords: list[str]
    skills: list[str]
    sources: list[SourceEntry]
    locations: list[str]
