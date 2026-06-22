"""Tests for loading the local Operator Credential from <settings-dir>/.env."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.runtime import ProviderAuth

from application_pipeline.operator_credential import (
    load_operator_credential,
)


def test_load_operator_credential_from_settings_env(tmp_path: Path) -> None:
    """A non-empty OPENCODE_GO_API_KEY in <settings-dir>/.env becomes ProviderAuth."""
    settings_dir = tmp_path / "application-pipeline"
    settings_dir.mkdir()
    (settings_dir / ".env").write_text(
        "OPENCODE_GO_API_KEY=local-key-from-env\n", encoding="utf-8"
    )

    credential = load_operator_credential(settings_dir)

    assert credential == ProviderAuth(opencode_api_key="local-key-from-env")
