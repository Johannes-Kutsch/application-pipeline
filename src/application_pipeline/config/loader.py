import importlib.util
import pathlib
import uuid

from .types import Config, ConfigError

_REQUIRED_FIELDS = ("KEYWORDS", "SKILLS", "SOURCES", "LOCATIONS")


def load(path: pathlib.Path) -> Config:
    resolved = path.resolve()
    if not resolved.exists():
        raise ConfigError(f"Config file does not exist: {resolved}")
    if not resolved.is_file():
        raise ConfigError(f"Config path is not a regular file: {resolved}")
    module_name = f"_application_pipeline_user_config_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Could not load config from {resolved}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SyntaxError as exc:
        raise ConfigError(f"Syntax error in config file {resolved}: {exc.msg}") from exc
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"Error executing config file {resolved}: {exc}") from exc

    for name in _REQUIRED_FIELDS:
        if not hasattr(module, name):
            raise ConfigError(f"Missing required field: {name}")

    config_dir = resolved.parent
    classify_prompt = _resolve_prompt(
        config_dir,
        getattr(
            module,
            "CLASSIFY_RELEVANCE_PROMPT",
            pathlib.Path("prompts/classify_relevance.md"),
        ),
    )
    judge_prompt = _resolve_prompt(
        config_dir,
        getattr(module, "JUDGE_MATCH_PROMPT", pathlib.Path("prompts/judge_match.md")),
    )
    _validate_prompt_file("CLASSIFY_RELEVANCE_PROMPT", classify_prompt)
    _validate_prompt_file("JUDGE_MATCH_PROMPT", judge_prompt)

    config = Config(
        keywords=module.KEYWORDS,
        skills=module.SKILLS,
        sources=module.SOURCES,
        locations=module.LOCATIONS,
        include_remote=getattr(module, "INCLUDE_REMOTE", False),
        classify_relevance_prompt=classify_prompt,
        judge_match_prompt=judge_prompt,
    )
    _validate(config)
    return config


def _resolve_prompt(config_dir: pathlib.Path, value: object) -> pathlib.Path:
    path = pathlib.Path(value)  # type: ignore[arg-type]
    if not path.is_absolute():
        path = config_dir / path
    return path


def _validate_prompt_file(name: str, path: pathlib.Path) -> None:
    if not path.is_file():
        raise ConfigError(f"{name} file does not exist: {path}")
    if path.stat().st_size == 0:
        raise ConfigError(f"{name} file is empty: {path}")


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
