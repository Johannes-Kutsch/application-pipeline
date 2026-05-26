import importlib
import logging
import pathlib

from application_pipeline.parsers.location import LocationCoverage, validate_coverage
from application_pipeline.user_settings import load_user_module

from .types import Config, ConfigError, SourceEntry, resolve_data_paths

_log = logging.getLogger(__name__)

_REQUIRED_FIELDS = ("SOURCES", "LOCATIONS")

_REMOVED_FIELDS = (
    "LAYOUT",
    "OLLAMA_BASE_URL",
    "OLLAMA_CLASSIFY_MODEL",
    "OLLAMA_JUDGE_MODEL",
    "OLLAMA_READ_TIMEOUT_SECONDS",
    "OLLAMA_JSON_RETRIES",
    "OLLAMA_HTTP_RETRIES",
    "OLLAMA_KEEP_ALIVE",
    "SEEN_STORE_PATH",
    "CLASSIFY_RELEVANCE_PROMPT",
    "JUDGE_MATCH_PROMPT",
    "USER_INFO_DIR",
)


def load(path: pathlib.Path) -> Config:
    module = load_user_module(path, ConfigError)

    for name in _REQUIRED_FIELDS:
        if not hasattr(module, name):
            raise ConfigError(f"Missing required field: {name}")

    for name in _REMOVED_FIELDS:
        if hasattr(module, name):
            raise ConfigError(
                f"{name} is no longer supported; remove it from your config"
            )

    config_dir = path.resolve().parent
    data_paths = resolve_data_paths(config_dir)

    seen_store_path = data_paths.seen_store_path

    user_info_dir = data_paths.user_info_dir

    raw_max_age = getattr(module, "MAX_LISTING_AGE_DAYS", 180)
    if isinstance(raw_max_age, bool) or not isinstance(raw_max_age, int):
        raise ConfigError("MAX_LISTING_AGE_DAYS must be an integer")
    max_listing_age_days = raw_max_age
    if max_listing_age_days < 1:
        raise ConfigError("MAX_LISTING_AGE_DAYS must be >= 1")

    raw_parallelism = getattr(module, "CLAUDE_CLASSIFY_PARALLELISM", 4)
    if isinstance(raw_parallelism, bool) or not isinstance(raw_parallelism, int):
        raise ConfigError("CLAUDE_CLASSIFY_PARALLELISM must be an integer")
    if raw_parallelism < 1:
        raise ConfigError("CLAUDE_CLASSIFY_PARALLELISM must be >= 1")

    raw_batch_size = getattr(module, "CLAUDE_CLASSIFY_BATCH_SIZE", 10)
    if isinstance(raw_batch_size, bool) or not isinstance(raw_batch_size, int):
        raise ConfigError("CLAUDE_CLASSIFY_BATCH_SIZE must be an integer")
    if raw_batch_size < 1:
        raise ConfigError("CLAUDE_CLASSIFY_BATCH_SIZE must be >= 1")

    raw_cooldown = getattr(module, "DEDUP_COOLDOWN_DAYS", 30)
    if isinstance(raw_cooldown, bool) or not isinstance(raw_cooldown, int):
        raise ConfigError("DEDUP_COOLDOWN_DAYS must be an integer")
    if raw_cooldown < 1:
        raise ConfigError("DEDUP_COOLDOWN_DAYS must be >= 1")

    if hasattr(module, "INCLUSION_KEYWORDS"):
        _log.info(
            "config has unused field 'INCLUSION_KEYWORDS' — safe to remove, see ADR-0026"
        )

    config = Config(
        sources=module.SOURCES,
        locations=module.LOCATIONS,
        include_remote=getattr(module, "INCLUDE_REMOTE", True),
        seen_store_path=seen_store_path,
        results_dir=data_paths.results_dir,
        failures_path=data_paths.failures_path,
        logs_path=data_paths.logs_path,
        user_info_dir=user_info_dir,
        claude_cli_path=getattr(module, "CLAUDE_CLI_PATH", None),
        max_listing_age_days=max_listing_age_days,
        claude_classify_parallelism=raw_parallelism,
        claude_classify_batch_size=raw_batch_size,
        dedup_cooldown_days=raw_cooldown,
    )
    _validate(config)
    return config


def _resolve_parser_modules(sources: list[SourceEntry]) -> list[LocationCoverage]:
    modules: list[LocationCoverage] = []
    for source in sources:
        try:
            modules.append(
                importlib.import_module(
                    f"application_pipeline.parsers.{source.parser_type}"
                )
            )
        except ImportError:
            pass
    return modules


def _validate(config: Config) -> None:
    if not config.sources:
        raise ConfigError("SOURCES must be non-empty")
    if not config.locations and not config.include_remote:
        raise ConfigError("nothing to search")

    if not config.user_info_dir.is_dir():
        raise ConfigError(
            f"USER_INFO_DIR: {config.user_info_dir} does not exist or is not a directory"
        )

    _check_unique("LOCATIONS", config.locations, item_label="value")
    _check_unique(
        "SOURCES",
        [entry.parser_type for entry in config.sources],
        item_label="parser_type",
    )
    validate_coverage(
        _resolve_parser_modules(config.sources),
        config.locations,
        config.include_remote,
    )


def _check_unique(name: str, values: list[str], *, item_label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ConfigError(f"{name} contains duplicate {item_label}: {value!r}")
        seen.add(value)
