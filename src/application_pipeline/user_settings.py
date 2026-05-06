import importlib.util
import pathlib
import types
import uuid


class UserSettingsError(Exception):
    pass


def load_user_module(
    path: pathlib.Path, error_class: type[UserSettingsError]
) -> types.ModuleType:
    resolved = path.resolve()
    if not resolved.exists():
        raise error_class(f"File does not exist: {resolved}")
    if not resolved.is_file():
        raise error_class(f"Path is not a regular file: {resolved}")
    module_name = f"_application_pipeline_user_module_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise error_class(f"Could not load module from {resolved}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except SyntaxError as exc:
        raise error_class(f"Syntax error in {resolved}: {exc.msg}") from exc
    except error_class:
        raise
    except Exception as exc:
        raise error_class(f"Error executing {resolved}: {exc}") from exc
    return module
