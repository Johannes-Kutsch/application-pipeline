"""Tests that pi-tick.sh and docs/pi-setup.md describe the canonical data/ layout."""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PI_TICK = REPO_ROOT / "scripts" / "pi-tick.sh"
PI_SETUP = REPO_ROOT / "docs" / "pi-setup.md"


def _script_text() -> str:
    return PI_TICK.read_text()


def _setup_text() -> str:
    return PI_SETUP.read_text()


# ── pi-tick.sh ────────────────────────────────────────────────────────────────


def test_pi_tick_defines_data_dir() -> None:
    text = _script_text()
    assert 'DATA_DIR="${BASE_DIR}/data"' in text


def test_pi_tick_no_synched_references() -> None:
    text = _script_text()
    assert "synched" not in text


def test_pi_tick_failures_dir_derived_from_data_dir() -> None:
    text = _script_text()
    assert 'FAILURES_DIR="${DATA_DIR}/failures"' in text


def test_pi_tick_init_targets_data_dir() -> None:
    text = _script_text()
    assert '"${DATA_DIR}"' in text
    assert 'application_pipeline init "${DATA_DIR}"' in text


def test_pi_tick_config_path_uses_data_dir() -> None:
    text = _script_text()
    assert '"${DATA_DIR}/config.py"' in text


# ── docs/pi-setup.md ─────────────────────────────────────────────────────────


def test_pi_setup_no_synched_references() -> None:
    text = _setup_text()
    assert "data/synched" not in text


def test_pi_setup_mkdir_uses_data_layout() -> None:
    text = _setup_text()
    assert "mkdir -p ~/application-pipeline/{releases,data/failures,data/logs}" in text


def test_pi_setup_syncthing_folder_path_uses_data() -> None:
    text = _setup_text()
    assert "/home/pi/application-pipeline/data" in text


def test_pi_setup_init_targets_data_dir() -> None:
    text = _setup_text()
    assert "application-pipeline/data/synched" not in text
    assert "application-pipeline/data" in text
