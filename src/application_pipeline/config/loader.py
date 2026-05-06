import pathlib

from application_pipeline.user_settings import load_user_module

from .types import Config, ConfigError

_REQUIRED_FIELDS = ("KEYWORDS", "SKILLS", "SOURCES", "LOCATIONS")


def load(path: pathlib.Path) -> Config:
    module = load_user_module(path, ConfigError)

    for name in _REQUIRED_FIELDS:
        if not hasattr(module, name):
            raise ConfigError(f"Missing required field: {name}")

    config_dir = path.resolve().parent
    classify_prompt = _load_prompt_path(
        "CLASSIFY_RELEVANCE_PROMPT",
        config_dir,
        getattr(
            module,
            "CLASSIFY_RELEVANCE_PROMPT",
            pathlib.Path("prompts/classify_relevance.md"),
        ),
    )
    judge_prompt = _load_prompt_path(
        "JUDGE_MATCH_PROMPT",
        config_dir,
        getattr(module, "JUDGE_MATCH_PROMPT", pathlib.Path("prompts/judge_match.md")),
    )

    config = Config(
        keywords=module.KEYWORDS,
        skills=module.SKILLS,
        sources=module.SOURCES,
        locations=module.LOCATIONS,
        include_remote=getattr(module, "INCLUDE_REMOTE", False),
        inclusion_keywords=getattr(module, "INCLUSION_KEYWORDS", []),
        negative_keywords=getattr(module, "NEGATIVE_KEYWORDS", []),
        classify_relevance_prompt=classify_prompt,
        judge_match_prompt=judge_prompt,
    )
    _validate(config)
    return config


def _load_prompt_path(
    name: str, config_dir: pathlib.Path, value: object
) -> pathlib.Path:
    path = pathlib.Path(value)  # type: ignore[arg-type]
    if not path.is_absolute():
        path = config_dir / path
    if not path.is_file():
        raise ConfigError(f"{name} file does not exist: {path}")
    if path.stat().st_size == 0:
        raise ConfigError(f"{name} file is empty: {path}")
    return path


def _validate(config: Config) -> None:
    if not config.keywords:
        raise ConfigError("KEYWORDS must be non-empty")
    if not config.sources:
        raise ConfigError("SOURCES must be non-empty")
    if not config.locations:
        raise ConfigError("LOCATIONS must be non-empty")

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


def _check_keyword_entries(name: str, values: list[str]) -> None:
    for entry in values:
        if len(entry) < 3:
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
