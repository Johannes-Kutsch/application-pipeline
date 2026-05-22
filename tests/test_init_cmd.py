from __future__ import annotations

import importlib.resources
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from application_pipeline import Config, Layout, load, load_layout
from application_pipeline.init_cmd import init
from application_pipeline.prompts import load_prompts


def _template_bytes(name: str) -> bytes:
    return (
        importlib.resources.files("application_pipeline.templates") / name
    ).read_bytes()


def _user_info_template_bytes(name: str) -> bytes:
    node = importlib.resources.files("application_pipeline.templates") / "user-info"
    for part in name.split("/"):
        node = node / part
    return node.read_bytes()


def _triage_profile_template_bytes(name: str) -> bytes:
    return (
        importlib.resources.files("application_pipeline.templates")
        / "user-info"
        / "triage-profile"
        / name
    ).read_bytes()


def _cv_template_bytes(name: str) -> bytes:
    return (
        importlib.resources.files("application_pipeline.templates")
        / "user-info"
        / "cv"
        / name
    ).read_bytes()


_TRIAGE_PROFILE_FILES = (
    "self-description.md",
    "domain-fit.md",
    "match-criteria.md",
    "writing-style.md",
)

_USER_INFO_ROOT_FILES = (
    "search-terms/keywords.md",
    "search-terms/skills.md",
    "search-terms/negative-keywords.md",
)

_USER_INFO_FILES = _TRIAGE_PROFILE_FILES + _USER_INFO_ROOT_FILES

_LATEX_USER_INFO_FILES = (
    "facts.tex",
    "content_pool.tex",
    "profile.png",
    "signature.png",
)


def test_first_bootstrap_writes_both_files(tmp_path: Path) -> None:
    init(tmp_path)

    assert (tmp_path / "config.py").read_bytes() == _template_bytes("config.py")
    assert (tmp_path / "layout.py").read_bytes() == _template_bytes("layout.py")


def test_config_template_contains_claude_classify_parallelism(tmp_path: Path) -> None:
    init(tmp_path)

    config_text = (tmp_path / "config.py").read_text()
    assert (
        "claude_classify_parallelism" in config_text.lower()
        or "CLAUDE_CLASSIFY_PARALLELISM" in config_text
    )
    assert "4" in config_text


def test_first_bootstrap_prints_wrote_for_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)

    out = capsys.readouterr().out
    assert "wrote config.py" in out
    assert "wrote layout.py" in out


def test_skip_existing_config_prints_correctly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config.py").write_text("# operator-edited\n")

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped config.py (already exists)" in out
    assert "wrote layout.py" in out


def test_both_exist_prints_skipped_for_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config.py").write_text("# custom\n")
    (tmp_path / "layout.py").write_text("# custom\n")

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped config.py (already exists)" in out
    assert "skipped layout.py (already exists)" in out


def test_skip_existing_config_preserves_content(tmp_path: Path) -> None:
    original = "# operator-edited content\nKEYWORDS = ['custom']\n"
    (tmp_path / "config.py").write_text(original)

    init(tmp_path)

    assert (tmp_path / "config.py").read_text() == original


def test_both_exist_neither_modified(tmp_path: Path) -> None:
    config_content = "# my config\n"
    layout_content = "# my layout\n"
    (tmp_path / "config.py").write_text(config_content)
    (tmp_path / "layout.py").write_text(layout_content)

    init(tmp_path)

    assert (tmp_path / "config.py").read_text() == config_content
    assert (tmp_path / "layout.py").read_text() == layout_content


def test_config_template_loads_successfully(tmp_path: Path) -> None:
    init(tmp_path)

    config = load(tmp_path / "config.py")

    assert isinstance(config, Config)
    assert config.sources
    assert config.include_remote is True


def test_layout_template_loads_successfully(tmp_path: Path) -> None:
    init(tmp_path)

    layout = load_layout(tmp_path / "layout.py")

    assert isinstance(layout, Layout)


# --- User-info file seeding ---


def test_fresh_seed_creates_user_info_tree(tmp_path: Path) -> None:
    init(tmp_path)

    assert (tmp_path / "config.py").exists()
    assert (tmp_path / "layout.py").exists()
    for fname in _TRIAGE_PROFILE_FILES:
        dest = tmp_path / "user-info" / "triage-profile" / fname
        assert dest.exists(), f"expected {dest} to be seeded"
        assert dest.read_bytes() == _triage_profile_template_bytes(fname)
    for fname in _USER_INFO_ROOT_FILES:
        dest = tmp_path / "user-info" / fname
        assert dest.exists(), f"expected {dest} to be seeded"
        assert dest.read_bytes() == _user_info_template_bytes(fname)


def test_fresh_seed_does_not_create_prompts_dir(tmp_path: Path) -> None:
    init(tmp_path)

    assert not (tmp_path / "prompts").exists()


def test_fresh_seed_does_not_create_latex_dir(tmp_path: Path) -> None:
    init(tmp_path)

    assert not (tmp_path / "latex").exists()


def test_fresh_seed_prints_all_five_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)

    out = capsys.readouterr().out
    assert "wrote config.py" in out
    assert "wrote layout.py" in out
    for fname in _TRIAGE_PROFILE_FILES:
        assert f"wrote user-info/triage-profile/{fname}" in out
    for fname in _USER_INFO_ROOT_FILES:
        assert f"wrote user-info/{fname}" in out


def test_seeded_config_and_user_info_load_prompts_without_error(tmp_path: Path) -> None:
    init(tmp_path)
    config = load(tmp_path / "config.py")

    prompts = load_prompts(config)

    from application_pipeline import PromptTemplate

    assert isinstance(prompts.classify_relevance, PromptTemplate)
    assert isinstance(prompts.judge_top_n, PromptTemplate)


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    init(tmp_path)
    first_contents = (
        {p: (tmp_path / p).read_bytes() for p in ["config.py", "layout.py"]}
        | {
            f"user-info/triage-profile/{f}": (
                tmp_path / "user-info" / "triage-profile" / f
            ).read_bytes()
            for f in _TRIAGE_PROFILE_FILES
        }
        | {
            f"user-info/{f}": (tmp_path / "user-info" / f).read_bytes()
            for f in _USER_INFO_ROOT_FILES
        }
    )

    init(tmp_path)

    for rel, original in first_contents.items():
        assert (tmp_path / rel).read_bytes() == original


def test_rerun_prints_all_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped config.py (already exists)" in out
    assert "skipped layout.py (already exists)" in out
    for fname in _TRIAGE_PROFILE_FILES:
        assert f"skipped user-info/triage-profile/{fname} (already exists)" in out
    for fname in _USER_INFO_ROOT_FILES:
        assert f"skipped user-info/{fname} (already exists)" in out


def test_per_file_skip_leaves_existing_user_info_and_seeds_siblings(
    tmp_path: Path,
) -> None:
    (tmp_path / "user-info" / "triage-profile").mkdir(parents=True)
    existing = tmp_path / "user-info" / "triage-profile" / "self-description.md"
    original_content = "# operator content\n"
    existing.write_text(original_content)

    init(tmp_path)

    assert existing.read_text() == original_content
    for fname in _TRIAGE_PROFILE_FILES:
        if fname != "self-description.md":
            assert (tmp_path / "user-info" / "triage-profile" / fname).exists(), (
                f"{fname} should be seeded"
            )
    for fname in _USER_INFO_ROOT_FILES:
        assert (tmp_path / "user-info" / fname).exists(), f"{fname} should be seeded"


def test_per_file_skip_granular_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "user-info" / "triage-profile").mkdir(parents=True)
    (tmp_path / "user-info" / "triage-profile" / "self-description.md").write_text(
        "# custom\n"
    )

    init(tmp_path)

    out = capsys.readouterr().out
    assert (
        "skipped user-info/triage-profile/self-description.md (already exists)" in out
    )
    for fname in _TRIAGE_PROFILE_FILES:
        if fname != "self-description.md":
            assert f"wrote user-info/triage-profile/{fname}" in out
    for fname in _USER_INFO_ROOT_FILES:
        assert f"wrote user-info/{fname}" in out


def test_banner_does_not_trigger_prompt_error(tmp_path: Path) -> None:
    init(tmp_path)
    config = load(tmp_path / "config.py")

    # load_prompts injects user-info content into package templates;
    # if any user-info template line uses raw {slot} syntax this would raise PromptError
    load_prompts(config)


# --- LaTeX per-applicant file seeding ---


def test_init_seeds_latex_user_info_files(tmp_path: Path) -> None:
    init(tmp_path)

    for fname in _LATEX_USER_INFO_FILES:
        dest = tmp_path / "user-info" / "cv" / fname
        assert dest.exists(), f"expected {dest} to be seeded by init"
        assert dest.read_bytes() == _cv_template_bytes(fname)


def test_init_seeds_subdirs_under_user_info(tmp_path: Path) -> None:
    init(tmp_path)

    user_info = tmp_path / "user-info"
    top_level = {p.name for p in user_info.iterdir()}
    assert top_level == {"triage-profile", "search-terms", "cv"}

    triage_names = {p.name for p in (user_info / "triage-profile").iterdir()}
    assert triage_names == set(_TRIAGE_PROFILE_FILES)

    cv_seeded = {p.name for p in (user_info / "cv").iterdir()}
    assert cv_seeded == set(_LATEX_USER_INFO_FILES)


def test_rerun_skips_existing_latex_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path)

    out = capsys.readouterr().out
    for fname in _LATEX_USER_INFO_FILES:
        assert f"skipped user-info/cv/{fname} (already exists)" in out


def test_rerun_preserves_latex_file_content(tmp_path: Path) -> None:
    init(tmp_path)
    facts_path = tmp_path / "user-info" / "cv" / "facts.tex"
    original = facts_path.read_bytes()

    init(tmp_path)

    assert facts_path.read_bytes() == original


def test_init_does_not_auto_migrate_existing_identity_and_contact(
    tmp_path: Path,
) -> None:
    (tmp_path / "user-info").mkdir()
    identity_content = "% user-edited identity\n\\firstname{Alice}\n"
    contact_content = "% user-edited contact\n\\address{Musterstr}{Berlin}{}\n"
    (tmp_path / "user-info" / "identity.tex").write_text(identity_content)
    (tmp_path / "user-info" / "contact.tex").write_text(contact_content)

    init(tmp_path)

    assert (tmp_path / "user-info" / "identity.tex").read_text() == identity_content
    assert (tmp_path / "user-info" / "contact.tex").read_text() == contact_content


# --- LaTeX package (application_pipeline.latex) ---

_LATEX_PACKAGE_FILES = ("cv_template.tex",)


def _latex_pkg_bytes(name: str) -> bytes:
    return (importlib.resources.files("application_pipeline.latex") / name).read_bytes()


def test_latex_package_files_are_accessible() -> None:
    for fname in _LATEX_PACKAGE_FILES:
        data = _latex_pkg_bytes(fname)
        assert len(data) > 0, f"expected non-empty content for {fname}"


# --- setup/*.sh seeding ---

_SETUP_SCRIPTS = ("cron.sh", "cron-install.sh", "cron-uninstall.sh")


def _setup_template_bytes(name: str) -> bytes:
    return (
        importlib.resources.files("application_pipeline.templates") / "setup" / name
    ).read_bytes()


def test_init_seeds_setup_scripts_with_correct_content(tmp_path: Path) -> None:
    init(tmp_path)

    for fname in _SETUP_SCRIPTS:
        assert (tmp_path / "setup" / fname).read_bytes() == _setup_template_bytes(fname)


def test_rerun_does_not_overwrite_existing_setup_scripts(tmp_path: Path) -> None:
    init(tmp_path)
    originals = {
        fname: (tmp_path / "setup" / fname).read_bytes() for fname in _SETUP_SCRIPTS
    }

    init(tmp_path)

    for fname in _SETUP_SCRIPTS:
        assert (tmp_path / "setup" / fname).read_bytes() == originals[fname]


def test_init_skips_existing_setup_scripts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "setup").mkdir()
    custom = "# custom cron\n"
    (tmp_path / "setup" / "cron.sh").write_text(custom)

    init(tmp_path)

    assert (tmp_path / "setup" / "cron.sh").read_text() == custom
    out = capsys.readouterr().out
    assert "skipped setup/cron.sh (already exists)" in out
    assert "wrote setup/cron-install.sh" in out
    assert "wrote setup/cron-uninstall.sh" in out


def test_cron_sh_invokes_init_refresh_without_path_arg(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (tmp_path / "setup" / "cron.sh").read_text()
    match = re.search(r"application-pipeline init --refresh(\S*)", cron_sh)
    assert match is not None
    assert match.group(1) == ""


def test_cron_sh_self_locates_via_dirname(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (tmp_path / "setup" / "cron.sh").read_text()
    assert 'cd "$(dirname "$0")/../.."' in cron_sh
    cd_pos = cron_sh.index('cd "$(dirname "$0")/../.."')
    assert cd_pos < cron_sh.index("pip ")
    assert cd_pos < cron_sh.index("application-pipeline ")


def test_cron_sh_flock_uses_project_root_relative_path(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (tmp_path / "setup" / "cron.sh").read_text()
    assert "application-pipeline/.cron.lock" in cron_sh


def test_cron_sh_pip_upgrade_warns_and_continues_on_failure(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (tmp_path / "setup" / "cron.sh").read_text()
    # pip failures must warn-and-continue, not call fail() or exit
    pip_lines = [ln for ln in cron_sh.splitlines() if "pip install" in ln]
    assert len(pip_lines) >= 2, "expected at least two pip install lines"
    for ln in pip_lines:
        assert "fail(" not in ln, f"pip line must not call fail(): {ln!r}"
        assert not re.search(r"\bexit\b", ln), f"pip line must not call exit: {ln!r}"


def test_cron_sh_both_pip_upgrade_attempts_run_unconditionally(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (tmp_path / "setup" / "cron.sh").read_text()
    pip_lines = [ln for ln in cron_sh.splitlines() if "pip install" in ln]
    assert len(pip_lines) >= 2, "expected at least two pip install upgrade lines"
    # both must target application-pipeline
    assert all("application-pipeline" in ln for ln in pip_lines)


def test_cron_sh_pip_warning_names_attempt_number(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (tmp_path / "setup" / "cron.sh").read_text()
    assert re.search(r"WARNING.*attempt 1", cron_sh)
    assert re.search(r"WARNING.*attempt 2", cron_sh)


def test_cron_sh_pip_warning_includes_captured_stderr(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (tmp_path / "setup" / "cron.sh").read_text()
    # stderr must be captured in a variable and echoed in the warning
    assert re.search(r"\$\(pip install.*2>&1", cron_sh)
    # the variable is referenced inside the warning echo
    assert re.search(r"WARNING.*\$_pip_stderr", cron_sh)


def test_cron_sh_pipeline_stages_still_call_fail_on_error(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (tmp_path / "setup" / "cron.sh").read_text()
    assert re.search(
        r"application-pipeline init --refresh.*\|\|.*\bfail\b", cron_sh, re.DOTALL
    )
    assert re.search(r"application-pipeline run.*\|\|.*\bfail\b", cron_sh, re.DOTALL)


def test_adr_0027_documents_pip_warn_and_continue_policy() -> None:
    adr_file = (
        Path(__file__).parent.parent
        / "docs/adr/0027-distribution-via-pypi-and-cron-upgrade.md"
    )
    text = adr_file.read_text()
    assert "warn" in text.lower() and "continue" in text.lower(), (
        "ADR-0027 must document the warn-and-continue policy for pip-upgrade failures"
    )
    assert "cron.log" in text, (
        "ADR-0027 must mention cron.log as the visibility mechanism"
    )


def test_cron_install_writes_weekday_only_schedule(tmp_path: Path) -> None:
    init(tmp_path)
    cron_install = (tmp_path / "setup" / "cron-install.sh").read_text()
    assert "30 0 * * 1-5" in cron_install
    assert "30 0 * * *" not in cron_install


def test_cron_install_command_is_absolute_path_only(tmp_path: Path) -> None:
    init(tmp_path)
    cron_install = (tmp_path / "setup" / "cron-install.sh").read_text()
    match = re.search(r"CRON_LINE=(.+)", cron_install)
    assert match is not None
    line = match.group(1)
    assert "cd " not in line


# --- setup/*.sh integration (smoke) ---


@pytest.fixture
def _isolated_crontab() -> Iterator[None]:
    """Back up the user's crontab around a test and restore it afterwards."""
    saved = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    had_crontab = saved.returncode == 0
    subprocess.run(["crontab", "-r"], check=False, capture_output=True)
    try:
        yield
    finally:
        subprocess.run(["crontab", "-r"], check=False, capture_output=True)
        if had_crontab:
            subprocess.run(["crontab", "-"], input=saved.stdout, text=True, check=False)


@pytest.mark.smoke
def test_cron_install_adds_crontab_line(
    tmp_path: Path, _isolated_crontab: None
) -> None:
    init(tmp_path)
    cron_install = tmp_path / "setup" / "cron-install.sh"

    result = subprocess.run(["bash", str(cron_install)], capture_output=True, text=True)
    assert result.returncode == 0

    crontab = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    line = crontab.stdout
    assert "30 0 * * 1-5" in line
    assert str(tmp_path / "setup" / "cron.sh") in line
    assert f"# application-pipeline:{tmp_path}" in line


@pytest.mark.smoke
def test_cron_uninstall_removes_only_this_marker(
    tmp_path: Path, _isolated_crontab: None
) -> None:
    init(tmp_path)
    cron_install = tmp_path / "setup" / "cron-install.sh"
    cron_uninstall = tmp_path / "setup" / "cron-uninstall.sh"

    foreign_line = "0 1 * * * /tmp/other.sh # application-pipeline:/some/other/dir"
    subprocess.run(["crontab", "-"], input=foreign_line + "\n", text=True, check=True)
    subprocess.run(["bash", str(cron_install)], check=True)

    result = subprocess.run(
        ["bash", str(cron_uninstall)], capture_output=True, text=True
    )
    assert result.returncode == 0

    crontab = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    assert f"# application-pipeline:{tmp_path}" not in crontab.stdout
    assert foreign_line in crontab.stdout


@pytest.mark.smoke
def test_cron_uninstall_no_op_on_empty_crontab(
    tmp_path: Path, _isolated_crontab: None
) -> None:
    init(tmp_path)
    cron_uninstall = tmp_path / "setup" / "cron-uninstall.sh"

    result = subprocess.run(
        ["bash", str(cron_uninstall)], capture_output=True, text=True
    )
    assert result.returncode == 0


# --- --refresh: overwrite global files, preserve user files ---


def test_refresh_console_output_distinguishes_overwrote_preserved_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    # Modify all files
    for fname in _SETUP_SCRIPTS:
        (tmp_path / "setup" / fname).write_text("# custom\n")
    (tmp_path / "config.py").write_text("# custom\n")
    (tmp_path / "layout.py").write_text("# custom\n")
    (tmp_path / "user-info" / "triage-profile" / "self-description.md").write_text(
        "# custom\n"
    )
    # Delete a global file to trigger "wrote"
    (tmp_path / "setup" / "cron-install.sh").unlink()
    capsys.readouterr()

    init(tmp_path, refresh=True)

    out = capsys.readouterr().out
    assert "overwrote setup/cron.sh" in out
    assert "overwrote setup/cron-uninstall.sh" in out
    assert "wrote setup/cron-install.sh" in out
    assert "skipped config.py (preserved)" in out
    assert "skipped layout.py (preserved)" in out
    assert "skipped user-info/triage-profile/self-description.md (preserved)" in out


def test_refresh_self_heals_missing_global_file(tmp_path: Path) -> None:
    init(tmp_path)
    (tmp_path / "layout.py").unlink()

    init(tmp_path, refresh=True)

    assert (tmp_path / "layout.py").read_bytes() == _template_bytes("layout.py")


def test_refresh_on_empty_dir_writes_all_files(tmp_path: Path) -> None:
    init(tmp_path, refresh=True)

    assert (tmp_path / "config.py").read_bytes() == _template_bytes("config.py")
    assert (tmp_path / "layout.py").read_bytes() == _template_bytes("layout.py")
    for fname in _SETUP_SCRIPTS:
        assert (tmp_path / "setup" / fname).read_bytes() == _setup_template_bytes(fname)
    for fname in _TRIAGE_PROFILE_FILES:
        assert (
            tmp_path / "user-info" / "triage-profile" / fname
        ).read_bytes() == _triage_profile_template_bytes(fname)
    for fname in _USER_INFO_ROOT_FILES:
        assert (
            tmp_path / "user-info" / fname
        ).read_bytes() == _user_info_template_bytes(fname)


def test_refresh_overwrites_setup_scripts_and_preserves_user_files(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    # Modify all files to simulate user edits
    custom_setup = "# user-modified setup\n"
    custom_config = "# user-modified config\n"
    custom_layout = "# user-modified layout\n"
    custom_user_info = "# user-modified self-description\n"
    for fname in _SETUP_SCRIPTS:
        (tmp_path / "setup" / fname).write_text(custom_setup)
    (tmp_path / "config.py").write_text(custom_config)
    (tmp_path / "layout.py").write_text(custom_layout)
    (tmp_path / "user-info" / "triage-profile" / "self-description.md").write_text(
        custom_user_info
    )

    init(tmp_path, refresh=True)

    # setup/ files must match package templates
    for fname in _SETUP_SCRIPTS:
        assert (tmp_path / "setup" / fname).read_bytes() == _setup_template_bytes(fname)
    # user files must retain user-modified content
    assert (tmp_path / "config.py").read_text() == custom_config
    assert (tmp_path / "layout.py").read_text() == custom_layout
    assert (
        tmp_path / "user-info" / "triage-profile" / "self-description.md"
    ).read_text() == custom_user_info


# --- skills/cv_skeleton.tex seeding ---


def _skills_template_bytes(name: str) -> bytes:
    return (
        importlib.resources.files("application_pipeline.templates") / "skills" / name
    ).read_bytes()


def test_fresh_init_creates_cv_skeleton(tmp_path: Path) -> None:
    init(tmp_path)

    dest = tmp_path / "skills" / "cv_skeleton.tex"
    assert dest.exists()
    assert dest.read_bytes() == _skills_template_bytes("cv_skeleton.tex")


def test_init_skips_existing_cv_skeleton(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    original = "% user-edited skeleton\n"
    (tmp_path / "skills" / "cv_skeleton.tex").write_text(original)

    init(tmp_path)

    assert (tmp_path / "skills" / "cv_skeleton.tex").read_text() == original


def test_refresh_overwrites_cv_skeleton(tmp_path: Path) -> None:
    init(tmp_path)
    (tmp_path / "skills" / "cv_skeleton.tex").write_text("% user-edited\n")

    init(tmp_path, refresh=True)

    assert (
        tmp_path / "skills" / "cv_skeleton.tex"
    ).read_bytes() == _skills_template_bytes("cv_skeleton.tex")


def test_refresh_prints_overwrote_for_cv_skeleton(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    (tmp_path / "skills" / "cv_skeleton.tex").write_text("% user-edited\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    out = capsys.readouterr().out
    assert "overwrote skills/cv_skeleton.tex" in out


def test_refresh_preserves_user_info_when_skills_exist(tmp_path: Path) -> None:
    init(tmp_path)
    custom_user_info = "# my self-description\n"
    (tmp_path / "user-info" / "triage-profile" / "self-description.md").write_text(
        custom_user_info
    )

    init(tmp_path, refresh=True)

    assert (
        tmp_path / "user-info" / "triage-profile" / "self-description.md"
    ).read_text() == custom_user_info
