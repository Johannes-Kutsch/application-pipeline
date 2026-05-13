import importlib
import os
import pathlib

from application_pipeline.parsers.location import LocationCoverage, validate_coverage
from application_pipeline.text.normalize import normalize
from application_pipeline.user_settings import load_user_module

from .types import Config, ConfigError, SourceEntry

_REQUIRED_FIELDS = ("KEYWORDS", "SKILLS", "SOURCES", "LOCATIONS")

_OLLAMA_FIELDS = (
    "OLLAMA_BASE_URL",
    "OLLAMA_CLASSIFY_MODEL",
    "OLLAMA_JUDGE_MODEL",
    "OLLAMA_READ_TIMEOUT_SECONDS",
    "OLLAMA_JSON_RETRIES",
    "OLLAMA_HTTP_RETRIES",
    "OLLAMA_KEEP_ALIVE",
)


def load(path: pathlib.Path) -> Config:
    module = load_user_module(path, ConfigError)

    for name in _REQUIRED_FIELDS:
        if not hasattr(module, name):
            raise ConfigError(f"Missing required field: {name}")

    for name in _OLLAMA_FIELDS:
        if hasattr(module, name):
            raise ConfigError(
                f"{name} is no longer supported; remove it from your config"
            )

    config_dir = path.resolve().parent

    seen_store_env = os.environ.get("SEEN_STORE_PATH")
    seen_store_path = (
        pathlib.Path(seen_store_env)
        if seen_store_env
        else pathlib.Path(getattr(module, "SEEN_STORE_PATH", ".seen.json"))
    )

    layout = _resolve_optional_file(
        "LAYOUT", config_dir, getattr(module, "LAYOUT", None)
    )

    prompts_dir = _resolve_dir(
        "PROMPTS_DIR",
        config_dir,
        getattr(module, "PROMPTS_DIR", pathlib.Path("prompts")),
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

    config = Config(
        keywords=module.KEYWORDS,
        skills=module.SKILLS,
        sources=module.SOURCES,
        locations=module.LOCATIONS,
        include_remote=getattr(module, "INCLUDE_REMOTE", True),
        inclusion_keywords=getattr(module, "INCLUSION_KEYWORDS", []),
        negative_keywords=getattr(module, "NEGATIVE_KEYWORDS", []),
        seen_store_path=seen_store_path,
        layout=layout,
        prompts_dir=prompts_dir,
        classify_relevance_prompt=classify_relevance_prompt,
        judge_match_prompt=judge_match_prompt,
        claude_cli_path=getattr(module, "CLAUDE_CLI_PATH", None),
        claude_classify_batch_size=claude_classify_batch_size,
    )
    _validate(config)
    return config


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
    if not config.keywords:
        raise ConfigError("KEYWORDS must be non-empty")
    if not config.sources:
        raise ConfigError("SOURCES must be non-empty")
    if not config.locations and not config.include_remote:
        raise ConfigError("nothing to search")

    if not config.prompts_dir.is_dir():
        raise ConfigError(
            f"PROMPTS_DIR: {config.prompts_dir} does not exist or is not a directory"
        )

    _check_unique("KEYWORDS", config.keywords, item_label="value")
    _check_unique("SKILLS", config.skills, item_label="value")
    _check_unique("LOCATIONS", config.locations, item_label="value")
    _check_unique(
        "SOURCES",
        [entry.parser_type for entry in config.sources],
        item_label="parser_type",
    )
    _check_keyword_entries("INCLUSION_KEYWORDS", config.inclusion_keywords)
    _check_keyword_entries("NEGATIVE_KEYWORDS", config.negative_keywords)
    validate_coverage(
        _resolve_parser_modules(config.sources),
        config.locations,
        config.include_remote,
    )


def _check_keyword_entries(name: str, values: list[str]) -> None:
    for entry in values:
        normalized = normalize(entry)
        if normalized is None or len(normalized) < 3:
            raise ConfigError(
                f"{name} entries must be at least 3 characters; got {entry!r}"
            )
    _check_unique(name, values, item_label="value")


def _check_unique(name: str, values: list[str], *, item_label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ConfigError(f"{name} contains duplicate {item_label}: {value!r}")
        seen.add(value)
