from __future__ import annotations

import importlib.resources
import re
from pathlib import Path

import pytest

from application_pipeline import Config, load
from application_pipeline.init_cmd import init
from application_pipeline.prompts import load_prompts


def _ap_template_bytes(name: str) -> bytes:
    node = (
        importlib.resources.files("application_pipeline.templates")
        / "application-pipeline"
    )
    for part in name.split("/"):
        node = node / part
    return node.read_bytes()


def _user_info_template_bytes(name: str) -> bytes:
    return _ap_template_bytes(f"user-info/{name}")


def _triage_profile_template_bytes(name: str) -> bytes:
    return _ap_template_bytes(f"user-info/triage-profile/{name}")


def _cv_template_bytes(name: str) -> bytes:
    return _ap_template_bytes(f"user-info/cv/{name}")


def _skeleton_template_bytes() -> bytes:
    return _ap_template_bytes("cv-template/cv_skeleton.tex")


def _agent_skill_template_bytes(name: str) -> bytes:
    return _ap_template_bytes(f"agent-skills/{name}")


def _claude_template_bytes(rel: str) -> bytes:
    node = importlib.resources.files("application_pipeline.templates") / "claude"
    for part in rel.split("/"):
        node = node / part
    return node.read_bytes()


def _claude_template_text(rel: str) -> str:
    return _claude_template_bytes(rel).decode()


def _analyse_listing_step_2(text: str) -> str:
    match = re.search(r"^2\. \*\*(?P<step>.+)$", text, flags=re.MULTILINE)
    assert match is not None
    return match.group("step")


_TRIAGE_PROFILE_FILES = (
    "candidate-profile.md",
    "gate-criteria.md",
    "skills.md",
)

_CV_MD_FILES = (
    "writing-style.md",
    "positive-exemplars.md",
)

_USER_INFO_ROOT_FILES = (
    "search-terms/keywords.md",
    "search-terms/negative-keywords.md",
)

_USER_INFO_FILES = _TRIAGE_PROFILE_FILES + _USER_INFO_ROOT_FILES

_LATEX_USER_INFO_FILES = (
    "facts.tex",
    "content_pool.tex",
    "profile.png",
    "signature.png",
)

_PKG_SKILL_DIRS = ("_shared", "analyse-listing", "iterate-cv", "write-cv")


def _ap(tmp: Path) -> Path:
    return tmp / "application-pipeline"


def _claude(tmp: Path) -> Path:
    return tmp / ".claude"


def test_first_bootstrap_writes_config(tmp_path: Path) -> None:
    init(tmp_path)

    assert (_ap(tmp_path) / "config.py").read_bytes() == _ap_template_bytes("config.py")
    assert not (_ap(tmp_path) / "layout.py").exists()


def test_config_template_contains_claude_classify_parallelism(tmp_path: Path) -> None:
    init(tmp_path)

    config_text = (_ap(tmp_path) / "config.py").read_text()
    assert "CLAUDE_CLASSIFY_PARALLELISM = 4" in config_text


def test_first_bootstrap_prints_wrote_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert "wrote" in lines[0]
    assert "layout.py" not in out


def test_skip_existing_config_prints_correctly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ap(tmp_path).mkdir()
    (_ap(tmp_path) / "config.py").write_text("# operator-edited\n")

    init(tmp_path)

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert "skipped" in lines[0]
    assert "layout.py" not in out


def test_both_exist_prints_skipped_for_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ap(tmp_path).mkdir()
    (_ap(tmp_path) / "config.py").write_text("# custom\n")
    (_ap(tmp_path) / "layout.py").write_text("# custom\n")

    init(tmp_path)

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert "skipped" in lines[0]
    assert "layout.py" not in out


def test_skip_existing_config_preserves_content(tmp_path: Path) -> None:
    _ap(tmp_path).mkdir()
    original = "# operator-edited content\nKEYWORDS = ['custom']\n"
    (_ap(tmp_path) / "config.py").write_text(original)

    init(tmp_path)

    assert (_ap(tmp_path) / "config.py").read_text() == original


def test_both_exist_neither_modified(tmp_path: Path) -> None:
    _ap(tmp_path).mkdir()
    config_content = "# my config\n"
    layout_content = "# my layout\n"
    (_ap(tmp_path) / "config.py").write_text(config_content)
    (_ap(tmp_path) / "layout.py").write_text(layout_content)

    init(tmp_path)

    assert (_ap(tmp_path) / "config.py").read_text() == config_content
    assert (_ap(tmp_path) / "layout.py").read_text() == layout_content


def test_config_template_loads_successfully(tmp_path: Path) -> None:
    init(tmp_path)

    config = load(_ap(tmp_path) / "config.py")

    assert isinstance(config, Config)
    assert config.sources
    assert config.include_remote is True


# --- User-info file seeding ---


def test_fresh_seed_creates_user_info_tree(tmp_path: Path) -> None:
    init(tmp_path)

    assert (_ap(tmp_path) / "config.py").exists()
    assert not (_ap(tmp_path) / "layout.py").exists()
    for fname in _TRIAGE_PROFILE_FILES:
        dest = _ap(tmp_path) / "user-info" / "triage-profile" / fname
        assert dest.exists(), f"expected {dest} to be seeded"
        assert dest.read_bytes() == _triage_profile_template_bytes(fname)
    for fname in _CV_MD_FILES:
        dest = _ap(tmp_path) / "user-info" / "cv" / fname
        assert dest.exists(), f"expected {dest} to be seeded"
        assert dest.read_bytes() == _cv_template_bytes(fname)
    for fname in _USER_INFO_ROOT_FILES:
        dest = _ap(tmp_path) / "user-info" / fname
        assert dest.exists(), f"expected {dest} to be seeded"
        assert dest.read_bytes() == _user_info_template_bytes(fname)


def test_fresh_seed_does_not_create_prompts_dir(tmp_path: Path) -> None:
    init(tmp_path)

    assert not (_ap(tmp_path) / "prompts").exists()


def test_fresh_seed_does_not_create_latex_dir(tmp_path: Path) -> None:
    init(tmp_path)

    assert not (_ap(tmp_path) / "latex").exists()


def test_fresh_seed_prints_all_five_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert "wrote" in lines[0]
    assert "layout.py" not in out


def test_seeded_config_and_user_info_load_prompts_without_error(tmp_path: Path) -> None:
    init(tmp_path)
    config = load(_ap(tmp_path) / "config.py")

    prompts = load_prompts(config)

    from application_pipeline import PromptTemplate

    assert isinstance(prompts.classify_relevance, PromptTemplate)
    assert isinstance(prompts.judge_top_n, PromptTemplate)


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    init(tmp_path)
    ap = _ap(tmp_path)
    first_contents = (
        {p: (ap / p).read_bytes() for p in ["config.py"]}
        | {
            f"user-info/triage-profile/{f}": (
                ap / "user-info" / "triage-profile" / f
            ).read_bytes()
            for f in _TRIAGE_PROFILE_FILES
        }
        | {
            f"user-info/cv/{f}": (ap / "user-info" / "cv" / f).read_bytes()
            for f in _CV_MD_FILES
        }
        | {
            f"user-info/{f}": (ap / "user-info" / f).read_bytes()
            for f in _USER_INFO_ROOT_FILES
        }
    )

    init(tmp_path)

    for rel, original in first_contents.items():
        assert (ap / rel).read_bytes() == original


def test_rerun_prints_all_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path)

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert "skipped" in lines[0]
    assert "layout.py" not in out


def test_per_file_skip_leaves_existing_user_info_and_seeds_siblings(
    tmp_path: Path,
) -> None:
    ap = _ap(tmp_path)
    (ap / "user-info" / "triage-profile").mkdir(parents=True)
    existing = ap / "user-info" / "triage-profile" / "candidate-profile.md"
    original_content = "# operator content\n"
    existing.write_text(original_content)

    init(tmp_path)

    assert existing.read_text() == original_content
    for fname in _TRIAGE_PROFILE_FILES:
        if fname != "candidate-profile.md":
            assert (ap / "user-info" / "triage-profile" / fname).exists(), (
                f"{fname} should be seeded"
            )
    for fname in _CV_MD_FILES:
        assert (ap / "user-info" / "cv" / fname).exists(), f"{fname} should be seeded"
    for fname in _USER_INFO_ROOT_FILES:
        assert (ap / "user-info" / fname).exists(), f"{fname} should be seeded"


def test_fresh_init_prints_single_summary_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert re.search(r"\d+", lines[0])
    assert "wrote" in lines[0]


def test_rerun_init_prints_single_summary_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path)

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert re.search(r"\d+", lines[0])
    assert "skipped" in lines[0]


def test_partial_init_prints_single_summary_line_with_both_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ap = _ap(tmp_path)
    (ap / "user-info" / "triage-profile").mkdir(parents=True)
    (ap / "user-info" / "triage-profile" / "candidate-profile.md").write_text(
        "# custom\n"
    )

    init(tmp_path)

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert "wrote" in lines[0]
    assert "skipped" in lines[0]
    assert re.search(r"\d+", lines[0])


def test_banner_does_not_trigger_prompt_error(tmp_path: Path) -> None:
    init(tmp_path)
    config = load(_ap(tmp_path) / "config.py")

    load_prompts(config)


# --- LaTeX per-applicant file seeding ---


def test_init_seeds_latex_user_info_files(tmp_path: Path) -> None:
    init(tmp_path)

    for fname in _LATEX_USER_INFO_FILES:
        dest = _ap(tmp_path) / "user-info" / "cv" / fname
        assert dest.exists(), f"expected {dest} to be seeded by init"
        assert dest.read_bytes() == _cv_template_bytes(fname)


def test_init_seeds_cv_style_files_with_cover_strategy_layout(tmp_path: Path) -> None:
    init(tmp_path)

    writing_style = (
        _ap(tmp_path) / "user-info" / "cv" / "writing-style.md"
    ).read_text()
    positive_exemplars = (
        _ap(tmp_path) / "user-info" / "cv" / "positive-exemplars.md"
    ).read_text()

    for heading in (
        "# Writing Style",
        "## Voice",
        "## Do",
        "## Don't",
        "## Register",
        "## Cover Strategy",
    ):
        assert heading in writing_style

    assert "Concrete exemplars do not belong here." in writing_style
    assert "Negative examples do not belong here." in positive_exemplars
    assert "negative examples do not belong" not in writing_style.lower()
    assert "## Negative Exemplars" not in positive_exemplars


def test_init_seeds_subdirs_under_user_info(tmp_path: Path) -> None:
    init(tmp_path)

    user_info = _ap(tmp_path) / "user-info"
    top_level = {p.name for p in user_info.iterdir()}
    assert top_level == {"triage-profile", "search-terms", "cv"}

    triage_names = {p.name for p in (user_info / "triage-profile").iterdir()}
    assert triage_names == set(_TRIAGE_PROFILE_FILES)

    cv_seeded = {p.name for p in (user_info / "cv").iterdir()}
    assert cv_seeded == set(_LATEX_USER_INFO_FILES) | set(_CV_MD_FILES)


def test_rerun_skips_existing_latex_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path)

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert "skipped" in lines[0]


def test_rerun_preserves_latex_file_content(tmp_path: Path) -> None:
    init(tmp_path)
    facts_path = _ap(tmp_path) / "user-info" / "cv" / "facts.tex"
    original = facts_path.read_bytes()

    init(tmp_path)

    assert facts_path.read_bytes() == original


def test_init_does_not_auto_migrate_existing_identity_and_contact(
    tmp_path: Path,
) -> None:
    ap = _ap(tmp_path)
    (ap / "user-info").mkdir(parents=True)
    identity_content = "% user-edited identity\n\\firstname{Alice}\n"
    contact_content = "% user-edited contact\n\\address{Musterstr}{Berlin}{}\n"
    (ap / "user-info" / "identity.tex").write_text(identity_content)
    (ap / "user-info" / "contact.tex").write_text(contact_content)

    init(tmp_path)

    assert (ap / "user-info" / "identity.tex").read_text() == identity_content
    assert (ap / "user-info" / "contact.tex").read_text() == contact_content


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
    return _ap_template_bytes(f"setup/{name}")


def test_init_seeds_setup_scripts_with_correct_content(tmp_path: Path) -> None:
    init(tmp_path)

    for fname in _SETUP_SCRIPTS:
        assert (_ap(tmp_path) / "setup" / fname).read_bytes() == _setup_template_bytes(
            fname
        )


def test_rerun_does_not_overwrite_existing_setup_scripts(tmp_path: Path) -> None:
    init(tmp_path)
    ap = _ap(tmp_path)
    originals = {fname: (ap / "setup" / fname).read_bytes() for fname in _SETUP_SCRIPTS}

    init(tmp_path)

    for fname in _SETUP_SCRIPTS:
        assert (ap / "setup" / fname).read_bytes() == originals[fname]


def test_init_skips_existing_setup_scripts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ap = _ap(tmp_path)
    (ap / "setup").mkdir(parents=True)
    custom = "# custom cron\n"
    (ap / "setup" / "cron.sh").write_text(custom)

    init(tmp_path)

    assert (ap / "setup" / "cron.sh").read_text() == custom
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert "wrote" in lines[0]
    assert "skipped" in lines[0]


def test_cron_sh_invokes_cron_subcommand(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (_ap(tmp_path) / "setup" / "cron.sh").read_text()
    assert re.search(r"application-pipeline cron", cron_sh)
    assert "init --refresh" not in cron_sh


def test_cron_sh_self_locates_via_dirname(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (_ap(tmp_path) / "setup" / "cron.sh").read_text()
    assert 'cd "$(dirname "$0")/../.."' in cron_sh
    cd_pos = cron_sh.index('cd "$(dirname "$0")/../.."')
    assert cd_pos < cron_sh.index("pip ")
    assert cd_pos < cron_sh.index("application-pipeline ")


def test_cron_sh_has_no_flock_or_cron_lock(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (_ap(tmp_path) / "setup" / "cron.sh").read_text()
    assert "flock" not in cron_sh
    assert ".cron.lock" not in cron_sh


def test_cron_sh_pip_upgrade_warns_and_continues_on_failure(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (_ap(tmp_path) / "setup" / "cron.sh").read_text()
    pip_lines = [ln for ln in cron_sh.splitlines() if "pip install" in ln]
    assert len(pip_lines) >= 2, "expected at least two pip install lines"
    for ln in pip_lines:
        assert "fail(" not in ln, f"pip line must not call fail(): {ln!r}"
        assert not re.search(r"\bexit\b", ln), f"pip line must not call exit: {ln!r}"


def test_cron_sh_both_pip_upgrade_attempts_run_unconditionally(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (_ap(tmp_path) / "setup" / "cron.sh").read_text()
    pip_lines = [ln for ln in cron_sh.splitlines() if "pip install" in ln]
    assert len(pip_lines) >= 2, "expected at least two pip install upgrade lines"
    assert all("application-pipeline" in ln for ln in pip_lines)


def test_cron_sh_pip_warning_names_attempt_number(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (_ap(tmp_path) / "setup" / "cron.sh").read_text()
    assert re.search(r"WARNING.*attempt 1", cron_sh)
    assert re.search(r"WARNING.*attempt 2", cron_sh)


def test_cron_sh_pip_warning_includes_captured_stderr(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (_ap(tmp_path) / "setup" / "cron.sh").read_text()
    assert re.search(r"\$\(\.venv/bin/pip install.*2>&1", cron_sh)
    assert re.search(r"WARNING.*\$_pip_stderr", cron_sh)


def test_cron_sh_has_no_fail_helper_or_init_run_stages(tmp_path: Path) -> None:
    init(tmp_path)
    cron_sh = (_ap(tmp_path) / "setup" / "cron.sh").read_text()
    assert "fail()" not in cron_sh
    assert "init --refresh" not in cron_sh
    assert not re.search(r"application-pipeline\s+run\b", cron_sh)


def test_adr_0020_documents_pip_warn_and_continue_policy() -> None:
    adr_file = (
        Path(__file__).parent.parent
        / "docs/adr/0020-distribution-via-pypi-and-cron-upgrade.md"
    )
    text = adr_file.read_text()
    assert "warn" in text.lower() and "continue" in text.lower(), (
        "ADR-0020 must document the warn-and-continue policy for pip-upgrade failures"
    )


def test_cron_install_writes_weekday_only_schedule(tmp_path: Path) -> None:
    init(tmp_path)
    cron_install = (_ap(tmp_path) / "setup" / "cron-install.sh").read_text()
    assert "30 0 * * 1-5" in cron_install
    assert "30 0 * * *" not in cron_install


def test_cron_install_command_is_absolute_path_only(tmp_path: Path) -> None:
    init(tmp_path)
    cron_install = (_ap(tmp_path) / "setup" / "cron-install.sh").read_text()
    match = re.search(r"CRON_LINE=(.+)", cron_install)
    assert match is not None
    line = match.group(1)
    assert "cd " not in line


# --- --refresh: overwrite global files, preserve user files ---


def test_refresh_console_output_distinguishes_overwrote_preserved_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    ap = _ap(tmp_path)
    # Modify all files
    for fname in _SETUP_SCRIPTS:
        (ap / "setup" / fname).write_text("# custom\n")
    (ap / "config.py").write_text("# custom\n")
    (ap / "layout.py").write_text("# legacy layout\n")
    (ap / "user-info" / "triage-profile" / "candidate-profile.md").write_text(
        "# custom\n"
    )
    # Delete a global file to trigger a new-file write
    (ap / "setup" / "cron-install.sh").unlink()
    capsys.readouterr()

    init(tmp_path, refresh=True)

    out = capsys.readouterr().out
    # Modified global files appear
    assert "overwrote setup/cron.sh" in out
    assert "overwrote setup/cron-uninstall.sh" in out
    # Legacy removal still appears
    assert "removed layout.py" in out
    # Suppressed: new file written during refresh, preserved user files
    assert "cron-install.sh" not in out
    assert "config.py" not in out
    assert "user-info" not in out


def test_refresh_removes_layout_py_if_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    (_ap(tmp_path) / "layout.py").write_text("# legacy\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert not (_ap(tmp_path) / "layout.py").exists()
    assert "removed layout.py" in capsys.readouterr().out


def test_refresh_on_empty_dir_writes_all_files(tmp_path: Path) -> None:
    init(tmp_path, refresh=True)

    ap = _ap(tmp_path)
    assert (ap / "config.py").read_bytes() == _ap_template_bytes("config.py")
    assert not (ap / "layout.py").exists()
    for fname in _SETUP_SCRIPTS:
        assert (ap / "setup" / fname).read_bytes() == _setup_template_bytes(fname)
    for fname in _TRIAGE_PROFILE_FILES:
        assert (
            ap / "user-info" / "triage-profile" / fname
        ).read_bytes() == _triage_profile_template_bytes(fname)
    for fname in _CV_MD_FILES:
        assert (ap / "user-info" / "cv" / fname).read_bytes() == _cv_template_bytes(
            fname
        )
    for fname in _USER_INFO_ROOT_FILES:
        assert (ap / "user-info" / fname).read_bytes() == _user_info_template_bytes(
            fname
        )


def test_refresh_overwrites_setup_scripts_and_preserves_user_files(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    ap = _ap(tmp_path)

    custom_setup = "# user-modified setup\n"
    custom_config = "# user-modified config\n"
    custom_user_info = "# user-modified self-description\n"
    for fname in _SETUP_SCRIPTS:
        (ap / "setup" / fname).write_text(custom_setup)
    (ap / "config.py").write_text(custom_config)
    (ap / "user-info" / "triage-profile" / "candidate-profile.md").write_text(
        custom_user_info
    )

    init(tmp_path, refresh=True)

    for fname in _SETUP_SCRIPTS:
        assert (ap / "setup" / fname).read_bytes() == _setup_template_bytes(fname)
    assert (ap / "config.py").read_text() == custom_config
    assert (
        ap / "user-info" / "triage-profile" / "candidate-profile.md"
    ).read_text() == custom_user_info


# --- cv-template/cv_skeleton.tex seeding ---


def test_fresh_init_creates_cv_skeleton(tmp_path: Path) -> None:
    init(tmp_path)

    dest = _ap(tmp_path) / "cv-template" / "cv_skeleton.tex"
    assert dest.exists()
    assert dest.read_bytes() == _skeleton_template_bytes()


def test_fresh_init_seeds_shared_agent_skill_bodies(tmp_path: Path) -> None:
    init(tmp_path)

    shared_root = _ap(tmp_path) / "agent-skills"
    expected_files = [
        "analyse-listing.md",
        "iterate-cv.md",
        "write-cv.md",
        "_shared/APPLICATION-FOLDER-ARG.md",
        "_shared/BUILD-CONTRACT.md",
        "_shared/CONVENTIONS.md",
        "_shared/SLOT-MAP.md",
        "_shared/STARTUP-APPLICATION.md",
        "_shared/STARTUP-TRIAGE.md",
        "_shared/STRIP-DOWN.md",
        "_shared/TRIAGE-ROUTING.md",
    ]

    assert shared_root.is_dir()
    assert (shared_root / "_shared").is_dir()
    for rel in expected_files:
        dest = shared_root / rel
        assert dest.exists(), f"expected {rel} to be seeded"
        assert dest.read_bytes() == _agent_skill_template_bytes(rel)


def test_seeded_shared_agent_skill_bodies_link_to_installed_shared_support(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    shared_root = _ap(tmp_path) / "agent-skills"
    for rel in ("analyse-listing.md", "iterate-cv.md", "write-cv.md"):
        text = (shared_root / rel).read_text()
        assert "../_shared/" not in text
        assert "_shared/" in text


def test_seeded_shared_agent_skill_support_files_reference_cv_template_path(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    shared_root = _ap(tmp_path) / "agent-skills" / "_shared"

    slot_map = (shared_root / "SLOT-MAP.md").read_text()
    assert "application-pipeline/cv-template/cv_skeleton.tex" in slot_map
    assert "application-pipeline/skills/cv_skeleton.tex" not in slot_map

    startup = (shared_root / "STARTUP-APPLICATION.md").read_text()
    assert "application-pipeline/cv-template/cv_skeleton.tex" in startup
    assert "application-pipeline/skills/cv_skeleton.tex" not in startup


def test_refresh_overwrites_shared_agent_skill_bodies(tmp_path: Path) -> None:
    init(tmp_path)
    skill_body = _ap(tmp_path) / "agent-skills" / "analyse-listing.md"
    skill_body.write_text("# tampered\n")

    init(tmp_path, refresh=True)

    assert skill_body.read_bytes() == _agent_skill_template_bytes("analyse-listing.md")


def test_refresh_overwrites_shared_agent_skill_support_files(tmp_path: Path) -> None:
    init(tmp_path)
    support_file = _ap(tmp_path) / "agent-skills" / "_shared" / "CONVENTIONS.md"
    support_file.write_text("# tampered\n")

    init(tmp_path, refresh=True)

    assert support_file.read_bytes() == _agent_skill_template_bytes(
        "_shared/CONVENTIONS.md"
    )


def test_init_skips_existing_cv_skeleton(tmp_path: Path) -> None:
    ap = _ap(tmp_path)
    (ap / "cv-template").mkdir(parents=True)
    original = "% user-edited skeleton\n"
    (ap / "cv-template" / "cv_skeleton.tex").write_text(original)

    init(tmp_path)

    assert (ap / "cv-template" / "cv_skeleton.tex").read_text() == original


def test_refresh_overwrites_cv_skeleton(tmp_path: Path) -> None:
    init(tmp_path)
    (_ap(tmp_path) / "cv-template" / "cv_skeleton.tex").write_text("% user-edited\n")

    init(tmp_path, refresh=True)

    assert (
        _ap(tmp_path) / "cv-template" / "cv_skeleton.tex"
    ).read_bytes() == _skeleton_template_bytes()


def test_refresh_prints_overwrote_for_cv_skeleton(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    (_ap(tmp_path) / "cv-template" / "cv_skeleton.tex").write_text("% user-edited\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    out = capsys.readouterr().out
    assert "overwrote cv-template/cv_skeleton.tex" in out


def test_refresh_preserves_user_info_when_skills_exist(tmp_path: Path) -> None:
    init(tmp_path)
    custom_user_info = "# my self-description\n"
    (
        _ap(tmp_path) / "user-info" / "triage-profile" / "candidate-profile.md"
    ).write_text(custom_user_info)

    init(tmp_path, refresh=True)

    assert (
        _ap(tmp_path) / "user-info" / "triage-profile" / "candidate-profile.md"
    ).read_text() == custom_user_info


def test_refresh_preserves_operator_owned_cv_style_files(tmp_path: Path) -> None:
    init(tmp_path)
    writing_style = _ap(tmp_path) / "user-info" / "cv" / "writing-style.md"
    positive_exemplars = _ap(tmp_path) / "user-info" / "cv" / "positive-exemplars.md"
    custom_writing_style = "# my writing rules\n"
    custom_positive_exemplars = "# my exemplars\n"
    writing_style.write_text(custom_writing_style)
    positive_exemplars.write_text(custom_positive_exemplars)

    init(tmp_path, refresh=True)

    assert writing_style.read_text() == custom_writing_style
    assert positive_exemplars.read_text() == custom_positive_exemplars


# --- legacy <cwd>/application-pipeline/skills/ cleanup on refresh ---


def test_refresh_removes_legacy_skills_dir_with_only_cv_skeleton(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    legacy = _ap(tmp_path) / "skills"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "cv_skeleton.tex").write_text("% stale\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert not legacy.exists()
    out = capsys.readouterr().out
    assert "removed skills/cv_skeleton.tex" in out
    assert "removed skills/" in out


def test_refresh_preserves_legacy_skills_dir_with_user_content(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    legacy = _ap(tmp_path) / "skills"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "cv_skeleton.tex").write_text("% stale\n")
    (legacy / "notes.md").write_text("# my notes\n")

    init(tmp_path, refresh=True)

    assert legacy.exists()
    assert not (legacy / "cv_skeleton.tex").exists()
    assert (legacy / "notes.md").read_text() == "# my notes\n"


def test_refresh_with_no_legacy_skills_dir_is_silent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path, refresh=True)

    out = capsys.readouterr().out
    assert "removed skills/" not in out


# --- .claude/skills/ seeding (ADR-0044) ---


def test_fresh_init_seeds_claude_skills(tmp_path: Path) -> None:
    init(tmp_path)

    claude_skills = _claude(tmp_path) / "skills"
    assert claude_skills.is_dir()
    for d in _PKG_SKILL_DIRS:
        assert (claude_skills / d).is_dir(), f"{d} missing"


def test_fresh_init_seeds_known_skill_files_with_template_content(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    claude_skills = _claude(tmp_path) / "skills"
    expected_files = [
        "_shared/STARTUP-TRIAGE.md",
        "_shared/TRIAGE-ROUTING.md",
        "analyse-listing/SKILL.md",
        "iterate-cv/SKILL.md",
        "write-cv/SKILL.md",
    ]
    for rel in expected_files:
        dest = claude_skills / rel
        assert dest.exists(), f"expected {rel} to be seeded"
        assert dest.read_bytes() == _claude_template_bytes(f"skills/{rel}")


def test_analyse_listing_step_2_compares_answers_against_triage_profile() -> None:
    text = _claude_template_text("skills/analyse-listing/SKILL.md")

    step = _analyse_listing_step_2(text)

    assert "bestehenden Triage-Profil-Bullets" in step
    assert "candidate-profile.md" in step
    assert "vertiefen, differenzieren oder korrigieren" in step
    assert "net-new" in step
    assert "gate-criteria.md" not in step
    assert "Domain-Fit" not in step
    assert "Match-Kriterien" not in step


def test_seeded_analyse_listing_step_2_keeps_enriched_profile_instruction(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    text = (_claude(tmp_path) / "skills" / "analyse-listing" / "SKILL.md").read_text()

    step = _analyse_listing_step_2(text)

    assert "candidate-profile.md" in step
    assert "vertiefen, differenzieren oder korrigieren" in step


def test_seeded_startup_triage_drops_domain_fit(tmp_path: Path) -> None:
    init(tmp_path)
    text = (_claude(tmp_path) / "skills" / "_shared" / "STARTUP-TRIAGE.md").read_text()
    assert "domain-fit.md" not in text
    assert "match-criteria.md" not in text
    assert "gate-criteria.md" in text


def test_seeded_triage_routing_drops_domain_fit(tmp_path: Path) -> None:
    init(tmp_path)
    text = (_claude(tmp_path) / "skills" / "_shared" / "TRIAGE-ROUTING.md").read_text()
    assert "domain-fit.md" not in text
    assert "match-criteria.md" not in text
    assert "gate-criteria.md" in text


def test_seeded_write_cv_skill_references_new_cv_template_path(tmp_path: Path) -> None:
    init(tmp_path)
    text = (_claude(tmp_path) / "skills" / "write-cv" / "SKILL.md").read_text()
    assert "application-pipeline/cv-template/cv_skeleton.tex" in text
    assert "application-pipeline/skills/cv_skeleton.tex" not in text


def test_seeded_iterate_cv_skill_references_new_cv_template_path(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    text = (_claude(tmp_path) / "skills" / "iterate-cv" / "SKILL.md").read_text()
    assert "application-pipeline/cv-template/cv_skeleton.tex" in text
    assert "application-pipeline/skills/cv_skeleton.tex" not in text


def test_refresh_overwrites_package_owned_skill_files(tmp_path: Path) -> None:
    init(tmp_path)
    skill_file = _claude(tmp_path) / "skills" / "_shared" / "STARTUP-TRIAGE.md"
    skill_file.write_text("# tampered\n")

    init(tmp_path, refresh=True)

    assert skill_file.read_bytes() == _claude_template_bytes(
        "skills/_shared/STARTUP-TRIAGE.md"
    )


def test_refresh_preserves_user_added_skill_dirs(tmp_path: Path) -> None:
    init(tmp_path)
    custom = _claude(tmp_path) / "skills" / "my-skill"
    custom.mkdir(parents=True, exist_ok=True)
    (custom / "SKILL.md").write_text("# my private skill\n")

    init(tmp_path, refresh=True)

    assert (custom / "SKILL.md").read_text() == "# my private skill\n"


def test_refresh_preserves_unknown_files_inside_package_owned_skill_dirs(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    notes = _claude(tmp_path) / "skills" / "iterate-cv" / "notes.md"
    notes.write_text("# wip\n")

    init(tmp_path, refresh=True)

    assert notes.read_text() == "# wip\n"


def test_init_creates_claude_dir_if_missing(tmp_path: Path) -> None:
    assert not (tmp_path / ".claude").exists()

    init(tmp_path)

    assert (tmp_path / ".claude").is_dir()


def test_init_does_not_touch_existing_claude_settings_local(tmp_path: Path) -> None:
    claude = _claude(tmp_path)
    claude.mkdir(parents=True, exist_ok=True)
    settings = claude / "settings.local.json"
    settings.write_text('{"foo": "bar"}\n')

    init(tmp_path, refresh=True)

    assert settings.read_text() == '{"foo": "bar"}\n'


def test_init_seeds_gitignore(tmp_path: Path) -> None:
    init(tmp_path)

    gitignore = _ap(tmp_path) / ".gitignore"
    assert gitignore.exists()
    assert gitignore.read_text() == ".runtime-data/\n"


def test_init_twice_leaves_gitignore_byte_identical(tmp_path: Path) -> None:
    init(tmp_path)
    first = (_ap(tmp_path) / ".gitignore").read_bytes()

    init(tmp_path)

    assert (_ap(tmp_path) / ".gitignore").read_bytes() == first


def test_refresh_preserves_gitignore_and_runtime_data(tmp_path: Path) -> None:
    ap = _ap(tmp_path)
    runtime_data = ap / ".runtime-data"
    runtime_data.mkdir(parents=True)
    seen = runtime_data / "seen.json"
    seen.write_text('{"custom": true}')

    gitignore = ap / ".gitignore"
    gitignore.write_text("# bespoke content\n")

    init(tmp_path, refresh=True)

    assert seen.read_text() == '{"custom": true}'
    assert gitignore.read_text() == "# bespoke content\n"


# --- --refresh quiet output (issue #664) ---


def test_refresh_against_unmodified_dir_prints_only_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path, refresh=True)

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    assert not any(
        line.startswith(("overwrote", "wrote", "skipped", "removed", "unchanged"))
        for line in lines
    )


def test_refresh_with_one_modified_file_prints_only_that_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    (_ap(tmp_path) / "setup" / "cron.sh").write_text("# modified\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines == ["overwrote setup/cron.sh"]


def test_refresh_config_and_gitignore_never_in_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path, refresh=True)

    out = capsys.readouterr().out
    assert "config.py" not in out
    assert ".gitignore" not in out


def test_refresh_new_file_created_but_not_in_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    (_ap(tmp_path) / "setup" / "cron-install.sh").unlink()
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert (_ap(tmp_path) / "setup" / "cron-install.sh").exists()
    assert "cron-install.sh" not in capsys.readouterr().out


def test_refresh_unchanged_file_preserves_mtime(tmp_path: Path) -> None:
    init(tmp_path)
    cron = _ap(tmp_path) / "setup" / "cron.sh"
    mtime_before = cron.stat().st_mtime_ns

    init(tmp_path, refresh=True)

    assert cron.stat().st_mtime_ns == mtime_before


def test_refresh_removed_lines_still_appear(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    (_ap(tmp_path) / "layout.py").write_text("# legacy\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert "removed layout.py" in capsys.readouterr().out
