import importlib.util
import pathlib
import uuid

from .types import Config, ConfigError

_REQUIRED_FIELDS = ("KEYWORDS", "SKILLS", "SOURCES", "LOCATIONS")


def load(path: pathlib.Path) -> Config:
    resolved = path.resolve()
    module_name = f"_application_pipeline_user_config_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Could not load config from {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for name in _REQUIRED_FIELDS:
        if not hasattr(module, name):
            raise ConfigError(f"Missing required field: {name}")

    relevance_prompt = getattr(module, "RELEVANCE_PROMPT_PATH", None)
    match_prompt = getattr(module, "MATCH_PROMPT_PATH", None)

    config = Config(
        keywords=module.KEYWORDS,
        skills=module.SKILLS,
        sources=module.SOURCES,
        locations=module.LOCATIONS,
        include_remote=getattr(module, "INCLUDE_REMOTE", False),
        relevance_prompt_path=(
            pathlib.Path(relevance_prompt) if relevance_prompt is not None else None
        ),
        match_prompt_path=(
            pathlib.Path(match_prompt) if match_prompt is not None else None
        ),
    )
    _validate(config)
    return config


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


def _check_unique(name: str, values: list[str], *, item_label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ConfigError(f"{name} contains duplicate {item_label}: {value!r}")
        seen.add(value)
