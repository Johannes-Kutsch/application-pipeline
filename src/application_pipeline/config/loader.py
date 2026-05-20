import importlib
import logging
import pathlib

from application_pipeline.parsers.location import LocationCoverage, validate_coverage
from application_pipeline.user_settings import load_user_module

from .types import Config, ConfigError, SourceEntry, resolve_data_paths

_log = logging.getLogger(__name__)

_REQUIRED_FIELDS = ("SOURCES", "LOCATIONS")

_MISSING = object()

_REMOVED_FIELDS = (
    "OLLAMA_BASE_URL",
    "OLLAMA_CLASSIFY_MODEL",
    "OLLAMA_JUDGE_MODEL",
    "OLLAMA_READ_TIMEOUT_SECONDS",
    "OLLAMA_JSON_RETRIES",
    "OLLAMA_HTTP_RETRIES",
    "OLLAMA_KEEP_ALIVE",
    "SEEN_STORE_PATH",
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

    layout = _resolve_layout("LAYOUT", config_dir, getattr(module, "LAYOUT", _MISSING))

    user_info_dir = _resolve_dir(
        "USER_INFO_DIR",
        config_dir,
        getattr(module, "USER_INFO_DIR", pathlib.Path("user-info")),
    )

    classify_relevance_prompt = _resolve_optional_file(
        "CLASSIFY_RELEVANCE_PROMPT",
        config_dir,
        getattr(module, "CLASSIFY_RELEVANCE_PROMPT", None),
        must_be_nonempty=True,
    )
    judge_match_prompt = _resolve_optional_file(
        "JUDGE_MATCH_PROMPT",
        config_dir,
        getattr(module, "JUDGE_MATCH_PROMPT", None),
        must_be_nonempty=True,
    )

    claude_classify_batch_size = int(getattr(module, "CLAUDE_CLASSIFY_BATCH_SIZE", 100))
    if claude_classify_batch_size < 1:
        raise ConfigError("CLAUDE_CLASSIFY_BATCH_SIZE must be >= 1")

    raw_max_age = getattr(module, "MAX_LISTING_AGE_DAYS", 180)
    if isinstance(raw_max_age, bool) or not isinstance(raw_max_age, int):
        raise ConfigError("MAX_LISTING_AGE_DAYS must be an integer")
    max_listing_age_days = raw_max_age
    if max_listing_age_days < 1:
        raise ConfigError("MAX_LISTING_AGE_DAYS must be >= 1")

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
        layout=layout,
        user_info_dir=user_info_dir,
        classify_relevance_prompt=classify_relevance_prompt,
        judge_match_prompt=judge_match_prompt,
        claude_cli_path=getattr(module, "CLAUDE_CLI_PATH", None),
        claude_classify_batch_size=claude_classify_batch_size,
        max_listing_age_days=max_listing_age_days,
    )
    _validate(config)
    return config


def _resolve_layout(
    name: str, config_dir: pathlib.Path, value: object
) -> pathlib.Path | None:
    if value is _MISSING:
        path = config_dir / "layout.py"
        if not path.is_file():
            raise ConfigError(
                f"{name}: {path} does not exist; add layout.py next to config.py"
                " or set LAYOUT = None to use the built-in default"
            )
        return path
    if value is None:
        return None
    return _resolve_optional_file(name, config_dir, value)


def _resolve_dir(name: str, config_dir: pathlib.Path, value: object) -> pathlib.Path:
    path = pathlib.Path(value)  # type: ignore[arg-type]
    if not path.is_absolute():
        path = config_dir / path
    return path


def _resolve_optional_file(
    name: str,
    config_dir: pathlib.Path,
    value: object,
    *,
    must_be_nonempty: bool = False,
) -> pathlib.Path | None:
    if value is None:
        return None
    path = pathlib.Path(value)  # type: ignore[arg-type]
    if not path.is_absolute():
        path = config_dir / path
    if not path.is_file():
        raise ConfigError(f"{name}: {path} does not exist or is not a file")
    if must_be_nonempty and path.stat().st_size == 0:
        raise ConfigError(f"{name}: {path} must not be empty")
    return path


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
