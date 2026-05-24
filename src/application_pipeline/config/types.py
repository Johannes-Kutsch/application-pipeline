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
    runtime = data_dir / ".runtime-data"
    return DataPaths(
        seen_store_path=runtime / "seen.json",
        results_dir=data_dir / "results",
        failures_path=runtime / "failures",
        logs_path=runtime / "logs",
        user_info_dir=data_dir / "user-info",
    )


@dataclass(frozen=True)
class SourceEntry:
    parser_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.parser_type, str) or not self.parser_type.strip():
            raise ConfigError("parser_type must be a non-empty, non-whitespace string")


@dataclass(frozen=True)
class Config:
    sources: list[SourceEntry]
    locations: list[str]
    include_remote: bool = True
    seen_store_path: pathlib.Path = field(
        default_factory=lambda: pathlib.Path(".runtime-data/seen.json")
    )
    results_dir: pathlib.Path = field(default_factory=lambda: pathlib.Path("results"))
    failures_path: pathlib.Path = field(
        default_factory=lambda: pathlib.Path(".runtime-data/failures")
    )
    logs_path: pathlib.Path = field(
        default_factory=lambda: pathlib.Path(".runtime-data/logs")
    )
    layout: pathlib.Path | None = None
    user_info_dir: pathlib.Path = field(
        default_factory=lambda: pathlib.Path("user-info")
    )
    claude_cli_path: str | None = None
    max_listing_age_days: int = 180
    claude_classify_parallelism: int = 4
