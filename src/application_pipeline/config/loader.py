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

    return Config(
        keywords=module.KEYWORDS,
        skills=module.SKILLS,
        sources=module.SOURCES,
        locations=module.LOCATIONS,
    )
