import tomllib
from pathlib import Path


_REPO_ROOT = Path(__file__).parent.parent


def _scm_config() -> dict:
    pyproject = _REPO_ROOT / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("setuptools_scm", {})


def _package_data_globs() -> list[str]:
    pyproject = _REPO_ROOT / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    return data["tool"]["setuptools"]["package-data"]["application_pipeline.templates"]


def test_local_scheme_is_no_local_version():
    assert _scm_config().get("local_scheme") == "no-local-version"


def test_version_scheme_is_guess_next_dev():
    assert _scm_config().get("version_scheme") == "guess-next-dev"


def test_fallback_version_is_0_0_0():
    assert _scm_config().get("fallback_version") == "0.0.0"


def test_package_data_uses_canonical_agent_skill_source_tree():
    globs = _package_data_globs()
    actual_dirs = {
        f"agent-skills/{path.name}/*"
        for path in (
            _REPO_ROOT / "src/application_pipeline/templates/agent-skills"
        ).iterdir()
        if path.is_dir()
    }

    assert actual_dirs == {glob for glob in globs if glob.startswith("agent-skills/")}


def test_package_data_excludes_retired_and_duplicated_agent_skill_roots():
    globs = _package_data_globs()

    assert not any(
        glob.startswith("application-pipeline/agent-skills/") for glob in globs
    )
    assert not any(glob.startswith("claude/skills/") for glob in globs)
    assert not any(glob.startswith("codex/skills/") for glob in globs)


def test_templates_keep_one_agent_skill_source_tree():
    templates_root = _REPO_ROOT / "src/application_pipeline/templates"

    assert (templates_root / "agent-skills").is_dir()
    assert not (templates_root / "claude/skills").exists()
    assert not (templates_root / "codex/skills").exists()
