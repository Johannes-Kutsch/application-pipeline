"""Tests for loading the local Operator Credential from <settings-dir>/.env."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.runtime import ProviderAuth
import pytest
from dotenv import load_dotenv

from application_pipeline.operator_credential import (
    OperatorCredentialError,
    load_operator_credential,
)


def test_load_operator_credential_from_settings_dir_env(tmp_path: Path) -> None:
    """A non-empty Operator Credential in <settings-dir>/.env becomes ProviderAuth."""
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir()
    (settings_dir / ".env").write_text(
        "OPENCODE_GO_API_KEY=local-key-from-env\n", encoding="utf-8"
    )

    credential = load_operator_credential(settings_dir)

    assert credential == ProviderAuth(opencode_api_key="local-key-from-env")


def test_load_operator_credential_from_utf8_sig_settings_dir_env(
    tmp_path: Path,
) -> None:
    """A UTF-8 BOM in <settings-dir>/.env does not hide the Operator Credential key."""
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir()
    (settings_dir / ".env").write_text(
        "OPENCODE_GO_API_KEY=local-key-from-env\n", encoding="utf-8-sig"
    )

    credential = load_operator_credential(settings_dir)

    assert credential == ProviderAuth(opencode_api_key="local-key-from-env")


def test_load_operator_credential_ignores_shell_env_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local Operator Credential wins over a different shell environment value."""
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir()
    (settings_dir / ".env").write_text(
        "OPENCODE_GO_API_KEY=local-key-from-env\n", encoding="utf-8"
    )
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "shell-key")

    credential = load_operator_credential(settings_dir)

    assert credential == ProviderAuth(opencode_api_key="local-key-from-env")


def test_load_operator_credential_ignores_home_env_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local Operator Credential wins over a different home-directory .env value."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    (home_dir / ".env").write_text("OPENCODE_GO_API_KEY=home-key\n", encoding="utf-8")
    load_dotenv(home_dir / ".env", override=True)
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "home-key")
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir()
    (settings_dir / ".env").write_text(
        "OPENCODE_GO_API_KEY=local-key-from-env\n", encoding="utf-8"
    )

    credential = load_operator_credential(settings_dir)

    assert credential == ProviderAuth(opencode_api_key="local-key-from-env")


def test_load_operator_credential_rejects_empty_settings_dir_value(
    tmp_path: Path,
) -> None:
    """An empty Operator Credential in <settings-dir>/.env is rejected at startup."""
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir()
    (settings_dir / ".env").write_text("OPENCODE_GO_API_KEY=\n", encoding="utf-8")

    with pytest.raises(OperatorCredentialError, match="missing non-empty"):
        load_operator_credential(settings_dir)
