from __future__ import annotations

from pathlib import Path

from agent_runtime.runtime import ProviderAuth

from application_pipeline.user_settings import UserSettingsError

_KEY_NAME = "OPENCODE_GO_API_KEY"


class OperatorCredentialError(UserSettingsError):
    pass


def load_operator_credential(settings_dir: Path) -> ProviderAuth:
    env_path = settings_dir / ".env"
    if not env_path.exists():
        raise OperatorCredentialError(
            f"{env_path.resolve()}: missing non-empty OPENCODE_GO_API_KEY"
        )
    if not env_path.is_file():
        raise OperatorCredentialError(
            f"Path is not a regular file: {env_path.resolve()}"
        )
    raw_value = _read_key_from_env_file(env_path, _KEY_NAME)
    if raw_value is None or not raw_value.strip():
        raise OperatorCredentialError(
            f"{env_path.resolve()}: missing non-empty {_KEY_NAME}"
        )
    return ProviderAuth(opencode_api_key=raw_value)


def _read_key_from_env_file(env_path: Path, key: str) -> str | None:
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        return value.strip()
    return None
