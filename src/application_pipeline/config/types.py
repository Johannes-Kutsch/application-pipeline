import pathlib
from dataclasses import dataclass, field

from application_pipeline.user_settings import UserSettingsError


class ConfigError(UserSettingsError):
    pass


@dataclass(frozen=True)
class DataPaths:
    seen_store_path: pathlib.Path
    results_dir: pathlib.Path
    failures_path: pathlib.Path
    logs_path: pathlib.Path
    user_info_dir: pathlib.Path


def resolve_data_paths(data_dir: pathlib.Path) -> DataPaths:
    return DataPaths(
        seen_store_path=data_dir / ".seen.json",
        results_dir=data_dir / "results",
        failures_path=data_dir / "failures",
        logs_path=data_dir / "logs",
        user_info_dir=data_dir / "user-info",
    )


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
    sources: list[SourceEntry]
    locations: list[str]
    include_remote: bool = True
    seen_store_path: pathlib.Path = field(
        default_factory=lambda: pathlib.Path(".seen.json")
    )
    results_dir: pathlib.Path = field(default_factory=lambda: pathlib.Path("results"))
    failures_path: pathlib.Path = field(
        default_factory=lambda: pathlib.Path("failures")
    )
    logs_path: pathlib.Path = field(default_factory=lambda: pathlib.Path("logs"))
    layout: pathlib.Path | None = None
    user_info_dir: pathlib.Path = field(
        default_factory=lambda: pathlib.Path("user-info")
    )
    classify_relevance_prompt: pathlib.Path | None = None
    judge_match_prompt: pathlib.Path | None = None
    claude_cli_path: str | None = None
    claude_classify_batch_size: int = 100
    max_listing_age_days: int = 180
