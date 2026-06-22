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
    node = importlib.resources.files("application_pipeline.templates") / "agent-skills"
    for part in name.split("/"):
        node = node / part
    return node.read_bytes()


def _skill_frontmatter(text: str) -> tuple[str, str]:
    match = re.match(
        r"^---\nname: (?P<name>[^\n]+)\ndescription: (?P<description>[^\n]+)\n---\n",
        text,
    )
    assert match is not None
    return match.group("name"), match.group("description")


_SKILL_DIRS = ("analyse-listing", "write-cv", "build-cv")


def _front_matter_field(text: str, field: str) -> str:
    match = re.search(rf"^{field}: .+$", text, flags=re.MULTILINE)
    assert match is not None
    return match.group(0).rstrip("\r")


def _assert_no_retired_skill_references(text: str) -> None:
    assert "application-pipeline/agent-skills/" not in text
    assert "application-pipeline/skills/" not in text


def _assert_seeded_skill_affordances(skill_file: Path, skill: str) -> str:
    text = skill_file.read_text(encoding="utf-8")
    assert skill_file.stat().st_size > 0
    name, description = _skill_frontmatter(text)
    assert name == skill
    assert description
    _assert_no_retired_skill_references(text)
    if skill == "analyse-listing":
        assert "application-pipeline/user-info/triage-profile/gate-criteria.md" in text
        assert "application-pipeline/user-info/triage-profile/candidate-profile.md" in (
            text
        )
    if skill in {"analyse-listing", "write-cv"}:
        assert "[_shared/CONVENTIONS.md](../_shared/CONVENTIONS.md)" in text
    if skill == "write-cv":
        assert "[_shared/SLOT-MAP.md](../_shared/SLOT-MAP.md)" in text
        assert "application-pipeline/user-info/cv/cover-patterns.md" in text
    if skill == "build-cv":
        assert "application-pipeline compile-cv <application-folder>" in text
        assert "cover_<application-folder>.pdf" in text
        assert "resume_<application-folder>.pdf" in text
        assert "combined_<application-folder>.pdf" in text
    return text


def _assert_seeded_shared_doc_affordances(shared_file: Path) -> str:
    text = shared_file.read_text(encoding="utf-8")
    assert shared_file.stat().st_size > 0
    _assert_no_retired_skill_references(text)
    if shared_file.name == "SLOT-MAP.md":
        assert "application-pipeline/cv-template/cv_skeleton.tex" in text
    return text


_TRIAGE_PROFILE_FILES = (
    "candidate-profile.md",
    "gate-criteria.md",
    "skills.md",
)

_CV_MD_FILES = ("cover-patterns.md",)

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


def _ap(tmp: Path) -> Path:
    return tmp / "application-pipeline"


def _claude(tmp: Path) -> Path:
    return tmp / ".claude"


def _codex(tmp: Path) -> Path:
    return tmp / ".codex"


def test_first_bootstrap_writes_config(tmp_path: Path) -> None:
    init(tmp_path)

    assert (_ap(tmp_path) / "config.py").read_bytes() == _ap_template_bytes("config.py")
    assert not (_ap(tmp_path) / "layout.py").exists()


def test_config_template_contains_claude_classify_parallelism(tmp_path: Path) -> None:
    init(tmp_path)

    config_text = (_ap(tmp_path) / "config.py").read_text(encoding="utf-8")
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


def test_fresh_init_prints_wrote_only_summary_form(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)

    assert re.fullmatch(r"wrote \d+ files\n", capsys.readouterr().out)


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


def test_rerun_init_prints_skipped_only_summary_form(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path)

    assert re.fullmatch(r"skipped \d+ files\n", capsys.readouterr().out)


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


def test_partial_init_prints_mixed_summary_form(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ap = _ap(tmp_path)
    (ap / "user-info" / "triage-profile").mkdir(parents=True)
    (ap / "user-info" / "triage-profile" / "candidate-profile.md").write_text(
        "# custom\n"
    )

    init(tmp_path)

    assert re.fullmatch(r"wrote \d+ files, skipped \d+\n", capsys.readouterr().out)


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


def test_init_seeds_cover_patterns_template(tmp_path: Path) -> None:
    init(tmp_path)

    cover_patterns = (
        _ap(tmp_path) / "user-info" / "cv" / "cover-patterns.md"
    ).read_text()

    assert "# Intro Patterns" in cover_patterns
    assert "## Product Resonance Intro" in cover_patterns
    assert "- slot: cover_intro" in cover_patterns
    assert "- argument_type: resonance" in cover_patterns
    assert "- placeholders: Musterfirma, Musterprodukt, Musterprojekt" in cover_patterns


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
    assert "wrote setup/cron-install.sh" in out
    # Legacy removal still appears
    assert "removed layout.py" in out
    # Suppressed: preserved user files
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


def test_fresh_init_seeds_inline_tool_skills_without_agent_skills_tree(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    assert not (_ap(tmp_path) / "agent-skills").exists()

    for root_name in (".claude", ".codex"):
        skills_root = tmp_path / root_name / "skills"
        assert skills_root.is_dir()
        for skill in _SKILL_DIRS:
            dest = skills_root / skill / "SKILL.md"
            assert dest.exists(), f"expected {root_name}/skills/{skill}/SKILL.md"
            _assert_seeded_skill_affordances(dest, skill)
        for rel in ("CONVENTIONS.md", "SLOT-MAP.md"):
            dest = skills_root / "_shared" / rel
            assert dest.exists(), f"expected {root_name}/skills/_shared/{rel}"
            _assert_seeded_shared_doc_affordances(dest)


def test_first_bootstrap_preserves_preexisting_application_pipeline_agent_skills(
    tmp_path: Path,
) -> None:
    legacy_skill = _ap(tmp_path) / "agent-skills" / "analyse-listing.md"
    legacy_skill.parent.mkdir(parents=True, exist_ok=True)
    legacy_skill.write_text("# operator-owned legacy body\n")

    init(tmp_path)

    assert legacy_skill.read_text() == "# operator-owned legacy body\n"
    assert (_claude(tmp_path) / "skills" / "analyse-listing" / "SKILL.md").exists()
    assert (_codex(tmp_path) / "skills" / "analyse-listing" / "SKILL.md").exists()


def test_seeded_inline_tool_skills_link_to_tool_local_shared_support(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    for root_name in (".claude", ".codex"):
        skills_root = tmp_path / root_name / "skills"
        for skill in ("analyse-listing", "write-cv", "build-cv"):
            _assert_seeded_skill_affordances(skills_root / skill / "SKILL.md", skill)


def test_fresh_init_seeds_write_cv_with_cover_pattern_library_affordances(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    for root in (_claude, _codex):
        _assert_seeded_skill_affordances(
            root(tmp_path) / "skills" / "write-cv" / "SKILL.md",
            "write-cv",
        )


def test_seeded_tool_local_shared_support_files_reference_cv_template_path(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    for root_name in (".claude", ".codex"):
        slot_map = tmp_path / root_name / "skills" / "_shared" / "SLOT-MAP.md"
        _assert_seeded_shared_doc_affordances(slot_map)


def test_fresh_init_materialises_byte_identical_agent_skill_runtime_files(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    for rel in (
        *(f"{skill}/SKILL.md" for skill in _SKILL_DIRS),
        "_shared/CONVENTIONS.md",
        "_shared/SLOT-MAP.md",
    ):
        claude_file = _claude(tmp_path) / "skills" / rel
        codex_file = _codex(tmp_path) / "skills" / rel
        assert claude_file.read_bytes() == codex_file.read_bytes()
        assert claude_file.read_bytes() == _agent_skill_template_bytes(rel)


def test_refresh_overwrites_inline_tool_skill_bodies_in_both_roots(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    claude_skill = _claude(tmp_path) / "skills" / "analyse-listing" / "SKILL.md"
    codex_skill = _codex(tmp_path) / "skills" / "analyse-listing" / "SKILL.md"
    claude_skill.write_text("# tampered claude\n")
    codex_skill.write_text("# tampered codex\n")

    init(tmp_path, refresh=True)

    assert claude_skill.read_text() != "# tampered claude\n"
    assert codex_skill.read_text() != "# tampered codex\n"
    _assert_seeded_skill_affordances(claude_skill, "analyse-listing")
    _assert_seeded_skill_affordances(codex_skill, "analyse-listing")


def test_first_bootstrap_seeds_missing_tool_skill_files_and_preserves_unknown_neighbors(
    tmp_path: Path,
) -> None:
    for root in (_claude(tmp_path), _codex(tmp_path)):
        skill_dir = root / "skills" / "write-cv"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "notes.md").write_text("# operator note\n")

    init(tmp_path)

    _assert_seeded_skill_affordances(
        _claude(tmp_path) / "skills" / "write-cv" / "SKILL.md",
        "write-cv",
    )
    _assert_seeded_skill_affordances(
        _codex(tmp_path) / "skills" / "write-cv" / "SKILL.md",
        "write-cv",
    )
    assert (
        _claude(tmp_path) / "skills" / "write-cv" / "notes.md"
    ).read_text() == "# operator note\n"
    assert (
        _codex(tmp_path) / "skills" / "write-cv" / "notes.md"
    ).read_text() == "# operator note\n"


def test_refresh_overwrites_tool_local_shared_support_files_in_both_roots(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    claude_support = _claude(tmp_path) / "skills" / "_shared" / "CONVENTIONS.md"
    codex_support = _codex(tmp_path) / "skills" / "_shared" / "CONVENTIONS.md"
    claude_support.write_text("# tampered claude\n")
    codex_support.write_text("# tampered codex\n")

    init(tmp_path, refresh=True)

    assert claude_support.read_text() != "# tampered claude\n"
    assert codex_support.read_text() != "# tampered codex\n"
    _assert_seeded_shared_doc_affordances(claude_support)
    _assert_seeded_shared_doc_affordances(codex_support)


def test_first_bootstrap_seeds_missing_tool_shared_files_and_preserves_unknown_neighbors(
    tmp_path: Path,
) -> None:
    for root in (_claude(tmp_path), _codex(tmp_path)):
        shared_dir = root / "skills" / "_shared"
        shared_dir.mkdir(parents=True, exist_ok=True)
        (shared_dir / "STARTUP-TRIAGE.md").write_text("# operator-local support\n")

    init(tmp_path)

    _assert_seeded_shared_doc_affordances(
        _claude(tmp_path) / "skills" / "_shared" / "CONVENTIONS.md"
    )
    _assert_seeded_shared_doc_affordances(
        _codex(tmp_path) / "skills" / "_shared" / "CONVENTIONS.md"
    )
    assert (
        _claude(tmp_path) / "skills" / "_shared" / "STARTUP-TRIAGE.md"
    ).read_text() == "# operator-local support\n"
    assert (
        _codex(tmp_path) / "skills" / "_shared" / "STARTUP-TRIAGE.md"
    ).read_text() == "# operator-local support\n"


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


def test_refresh_preserves_operator_owned_cover_patterns(tmp_path: Path) -> None:
    init(tmp_path)
    cover_patterns = _ap(tmp_path) / "user-info" / "cv" / "cover-patterns.md"
    custom_cover_patterns = "# my cover patterns\n"
    cover_patterns.write_text(custom_cover_patterns)

    init(tmp_path, refresh=True)

    assert cover_patterns.read_text() == custom_cover_patterns


def test_refresh_seeds_missing_cover_patterns_file(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    cover_patterns = _ap(tmp_path) / "user-info" / "cv" / "cover-patterns.md"
    cover_patterns.unlink()

    init(tmp_path, refresh=True)

    assert cover_patterns.read_bytes() == _cv_template_bytes("cover-patterns.md")


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


# --- retired iterate-cv cleanup on refresh ---


def test_refresh_preserves_application_pipeline_agent_skills_while_removing_retired_tool_wrappers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    iterate_cv_md = _ap(tmp_path) / "agent-skills" / "iterate-cv.md"
    iterate_cv_md.parent.mkdir(parents=True, exist_ok=True)
    iterate_cv_md.write_text("# old body\n")
    claude_skill = _claude(tmp_path) / "skills" / "iterate-cv" / "SKILL.md"
    claude_skill.parent.mkdir(parents=True, exist_ok=True)
    claude_skill.write_text("# old claude\n")
    codex_skill = _codex(tmp_path) / "skills" / "iterate-cv" / "SKILL.md"
    codex_skill.parent.mkdir(parents=True, exist_ok=True)
    codex_skill.write_text("# old codex\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert iterate_cv_md.read_text() == "# old body\n"
    assert not claude_skill.exists()
    assert not codex_skill.exists()
    out = capsys.readouterr().out
    assert "agent-skills/iterate-cv.md" not in out
    assert "removed .claude/skills/iterate-cv/SKILL.md" in out
    assert "removed .codex/skills/iterate-cv/SKILL.md" in out


def test_refresh_preserves_legacy_application_pipeline_agent_skills_shared_docs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    legacy_shared = _ap(tmp_path) / "agent-skills" / "_shared" / "CONVENTIONS.md"
    legacy_shared.parent.mkdir(parents=True, exist_ok=True)
    legacy_shared.write_text("# operator-owned legacy shared doc\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert legacy_shared.read_text() == "# operator-owned legacy shared doc\n"
    assert "agent-skills/_shared/CONVENTIONS.md" not in capsys.readouterr().out


def test_refresh_prunes_empty_parent_dir_after_removing_iterate_cv_skill(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    claude_skill = _claude(tmp_path) / "skills" / "iterate-cv" / "SKILL.md"
    claude_skill.parent.mkdir(parents=True, exist_ok=True)
    claude_skill.write_text("# old\n")

    init(tmp_path, refresh=True)

    assert not claude_skill.parent.exists()


def test_refresh_preserves_user_files_in_retired_iterate_cv_dir(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    skill_dir = _claude(tmp_path) / "skills" / "iterate-cv"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# old\n")
    notes = skill_dir / "notes.md"
    notes.write_text("# my notes\n")

    init(tmp_path, refresh=True)

    assert not (skill_dir / "SKILL.md").exists()
    assert skill_dir.exists()
    assert notes.read_text() == "# my notes\n"


def test_normal_init_does_not_run_refresh_cleanup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    legacy_layout = _ap(tmp_path) / "layout.py"
    legacy_layout.write_text("# legacy\n")
    legacy_skill = _ap(tmp_path) / "agent-skills" / "iterate-cv.md"
    legacy_skill.parent.mkdir(parents=True, exist_ok=True)
    legacy_skill.write_text("# old body\n")
    legacy_cv_skeleton = _ap(tmp_path) / "skills" / "cv_skeleton.tex"
    legacy_cv_skeleton.parent.mkdir(parents=True, exist_ok=True)
    legacy_cv_skeleton.write_text("% stale\n")
    capsys.readouterr()

    init(tmp_path)

    assert legacy_layout.exists()
    assert legacy_skill.exists()
    assert legacy_cv_skeleton.exists()
    out = capsys.readouterr().out
    assert "layout.py" not in out
    assert "iterate-cv" not in out
    assert "skills/cv_skeleton.tex" not in out


def test_refresh_is_silent_when_retired_iterate_cv_files_absent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path, refresh=True)

    out = capsys.readouterr().out
    assert "iterate-cv" not in out


def test_refresh_silently_prunes_empty_retired_iterate_cv_parent_dirs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    claude_skill = _claude(tmp_path) / "skills" / "iterate-cv" / "SKILL.md"
    claude_skill.parent.mkdir(parents=True, exist_ok=True)
    claude_skill.write_text("# old\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    lines = capsys.readouterr().out.splitlines()
    assert "removed .claude/skills/iterate-cv/SKILL.md" in lines
    assert "removed .claude/skills/iterate-cv" not in lines
    assert "removed .claude/skills/" not in lines


# --- .codex/skills/ seeding (issue #689) ---


def test_fresh_init_seeds_codex_skill_wrappers_with_claude_metadata(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    codex_skills = _codex(tmp_path) / "skills"
    assert codex_skills.is_dir()
    shared_dir = codex_skills / "_shared"
    assert shared_dir.is_dir()
    _assert_seeded_shared_doc_affordances(shared_dir / "CONVENTIONS.md")
    _assert_seeded_shared_doc_affordances(shared_dir / "SLOT-MAP.md")
    for d in _SKILL_DIRS:
        wrapper = codex_skills / d / "SKILL.md"
        assert wrapper.exists(), f"{d}/SKILL.md missing"
        codex_text = _assert_seeded_skill_affordances(wrapper, d)
        claude_text = (_claude(tmp_path) / "skills" / d / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert _front_matter_field(codex_text, "name") == _front_matter_field(
            claude_text, "name"
        )
        assert _front_matter_field(codex_text, "description") == _front_matter_field(
            claude_text, "description"
        )

    assert not (_ap(tmp_path) / ".codex").exists()


def test_refresh_overwrites_package_owned_codex_skill_files(tmp_path: Path) -> None:
    init(tmp_path)
    skill_file = _codex(tmp_path) / "skills" / "analyse-listing" / "SKILL.md"
    skill_file.write_text("# tampered\n")

    init(tmp_path, refresh=True)

    assert skill_file.read_text() != "# tampered\n"
    _assert_seeded_skill_affordances(skill_file, "analyse-listing")


def test_refresh_preserves_user_added_codex_skill_dirs(tmp_path: Path) -> None:
    init(tmp_path)
    custom = _codex(tmp_path) / "skills" / "my-skill"
    custom.mkdir(parents=True, exist_ok=True)
    (custom / "SKILL.md").write_text("# my private skill\n")

    init(tmp_path, refresh=True)

    assert (custom / "SKILL.md").read_text() == "# my private skill\n"


def test_refresh_preserves_unknown_files_inside_package_owned_codex_skill_dirs(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    notes = _codex(tmp_path) / "skills" / "write-cv" / "notes.md"
    notes.write_text("# wip\n")

    init(tmp_path, refresh=True)

    assert notes.read_text() == "# wip\n"


def test_refresh_preserves_operator_notes_at_codex_skills_root(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    notes = _codex(tmp_path) / "skills" / "README.md"
    notes.write_text("# private notes\n")

    init(tmp_path, refresh=True)

    assert notes.read_text() == "# private notes\n"


def test_refresh_preserves_preexisting_codex_adapter_local_shared_dir(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    shared_dir = _codex(tmp_path) / "skills" / "_shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    support_file = shared_dir / "STARTUP-TRIAGE.md"
    support_file.write_text("# operator-local support\n")

    init(tmp_path, refresh=True)

    assert support_file.read_text() == "# operator-local support\n"


def test_refresh_restores_missing_codex_wrapper_and_preserves_neighboring_user_files(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    skill_dir = _codex(tmp_path) / "skills" / "write-cv"
    skill_file = skill_dir / "SKILL.md"
    notes = skill_dir / "notes.md"
    notes.write_text("# wip\n")
    skill_file.unlink()

    init(tmp_path, refresh=True)

    _assert_seeded_skill_affordances(skill_file, "write-cv")
    assert notes.read_text() == "# wip\n"


# --- .claude/skills/ seeding (ADR-0044) ---


def test_fresh_init_seeds_claude_skills(tmp_path: Path) -> None:
    init(tmp_path)

    claude_skills = _claude(tmp_path) / "skills"
    assert claude_skills.is_dir()
    shared_dir = claude_skills / "_shared"
    assert shared_dir.is_dir()
    _assert_seeded_shared_doc_affordances(shared_dir / "CONVENTIONS.md")
    _assert_seeded_shared_doc_affordances(shared_dir / "SLOT-MAP.md")
    for d in _SKILL_DIRS:
        assert (claude_skills / d).is_dir(), f"{d} missing"


def test_fresh_init_seeds_claude_skill_templates_with_inlined_workflows(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    for skill in _SKILL_DIRS:
        skill_file = _claude(tmp_path) / "skills" / skill / "SKILL.md"
        _assert_seeded_skill_affordances(skill_file, skill)


def test_init_seeds_matching_skill_metadata_in_both_tool_roots(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    for skill in _SKILL_DIRS:
        claude = _assert_seeded_skill_affordances(
            _claude(tmp_path) / "skills" / skill / "SKILL.md",
            skill,
        )
        codex = _assert_seeded_skill_affordances(
            _codex(tmp_path) / "skills" / skill / "SKILL.md",
            skill,
        )
        assert _front_matter_field(claude, "name") == _front_matter_field(codex, "name")
        assert _front_matter_field(claude, "description") == _front_matter_field(
            codex, "description"
        )


def test_fresh_init_seeds_known_skill_files_with_template_content(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    claude_skills = _claude(tmp_path) / "skills"
    expected_files = [f"{skill}/SKILL.md" for skill in _SKILL_DIRS]
    for rel in expected_files:
        dest = claude_skills / rel
        assert dest.exists(), f"expected {rel} to be seeded"
        _assert_seeded_skill_affordances(dest, Path(rel).parent.name)


def test_refresh_overwrites_package_owned_skill_files(tmp_path: Path) -> None:
    init(tmp_path)
    skill_file = _claude(tmp_path) / "skills" / "write-cv" / "SKILL.md"
    skill_file.write_text("# tampered\n")

    init(tmp_path, refresh=True)

    assert skill_file.read_text() != "# tampered\n"
    _assert_seeded_skill_affordances(skill_file, "write-cv")


def test_refresh_preserves_preexisting_adapter_local_shared_dir(tmp_path: Path) -> None:
    init(tmp_path)
    shared_dir = _claude(tmp_path) / "skills" / "_shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    support_file = shared_dir / "STARTUP-TRIAGE.md"
    support_file.write_text("# operator-local support\n")

    init(tmp_path, refresh=True)

    assert support_file.read_text() == "# operator-local support\n"


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
    notes = _claude(tmp_path) / "skills" / "write-cv" / "notes.md"
    notes.write_text("# wip\n")

    init(tmp_path, refresh=True)

    assert notes.read_text() == "# wip\n"


def test_refresh_preserves_operator_notes_at_claude_skills_root(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    notes = _claude(tmp_path) / "skills" / "README.md"
    notes.write_text("# private notes\n")

    init(tmp_path, refresh=True)

    assert notes.read_text() == "# private notes\n"


def test_refresh_restores_missing_wrapper_and_preserves_neighboring_user_files(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    skill_dir = _claude(tmp_path) / "skills" / "write-cv"
    skill_file = skill_dir / "SKILL.md"
    notes = skill_dir / "notes.md"
    notes.write_text("# wip\n")
    skill_file.unlink()

    init(tmp_path, refresh=True)

    _assert_seeded_skill_affordances(skill_file, "write-cv")
    assert notes.read_text() == "# wip\n"


def test_refresh_reports_changed_agent_skill_artifacts_with_bucketed_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    claude_wrapper = _claude(tmp_path) / "skills" / "write-cv" / "SKILL.md"
    codex_wrapper = _codex(tmp_path) / "skills" / "analyse-listing" / "SKILL.md"
    claude_shared = _claude(tmp_path) / "skills" / "_shared" / "CONVENTIONS.md"
    codex_shared = _codex(tmp_path) / "skills" / "_shared" / "SLOT-MAP.md"
    legacy_agent_skill = _ap(tmp_path) / "agent-skills" / "analyse-listing.md"
    legacy_agent_skill.parent.mkdir(parents=True, exist_ok=True)

    legacy_agent_skill.write_text("# operator-owned legacy body\n")
    claude_wrapper.write_text("# tampered claude wrapper\n")
    codex_wrapper.write_text("# tampered codex wrapper\n")
    claude_shared.write_text("# tampered claude shared\n")
    codex_shared.write_text("# tampered codex shared\n")

    user_claude_skill = _claude(tmp_path) / "skills" / "my-skill"
    user_claude_skill.mkdir(parents=True, exist_ok=True)
    (user_claude_skill / "SKILL.md").write_text("# private skill\n")

    user_codex_note = _codex(tmp_path) / "skills" / "write-cv" / "notes.md"
    user_codex_note.write_text("# private note\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert "overwrote .claude/skills/write-cv/SKILL.md" in lines
    assert "overwrote .claude/skills/_shared/CONVENTIONS.md" in lines
    assert "overwrote .codex/skills/analyse-listing/SKILL.md" in lines
    assert "overwrote .codex/skills/_shared/SLOT-MAP.md" in lines
    assert not any("agent-skills/" in line for line in lines)
    assert not any("my-skill" in line for line in lines)
    assert not any("notes.md" in line for line in lines)


def test_refresh_restores_byte_identical_agent_skill_runtime_files(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    (_claude(tmp_path) / "skills" / "write-cv" / "SKILL.md").write_text(
        "# tampered claude wrapper\n"
    )
    (_codex(tmp_path) / "skills" / "_shared" / "SLOT-MAP.md").write_text(
        "# tampered codex shared\n"
    )

    init(tmp_path, refresh=True)

    for rel in ("write-cv/SKILL.md", "_shared/SLOT-MAP.md"):
        claude_file = _claude(tmp_path) / "skills" / rel
        codex_file = _codex(tmp_path) / "skills" / rel
        assert claude_file.read_bytes() == codex_file.read_bytes()
        assert claude_file.read_bytes() == _agent_skill_template_bytes(rel)


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


def test_refresh_prints_only_visible_actions_for_mixed_refresh_outcomes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    ap = _ap(tmp_path)
    (ap / "setup" / "cron.sh").write_text("# modified\n")
    (ap / "setup" / "cron-install.sh").unlink()
    (ap / "config.py").write_text("# custom\n")
    (ap / "layout.py").write_text("# legacy\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines == [
        "wrote setup/cron-install.sh",
        "overwrote setup/cron.sh",
        "removed layout.py",
    ]


def test_refresh_config_and_gitignore_never_in_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path, refresh=True)

    out = capsys.readouterr().out
    assert "config.py" not in out
    assert ".gitignore" not in out


def test_refresh_new_package_owned_file_is_reported_in_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    (_ap(tmp_path) / "setup" / "cron-install.sh").unlink()
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert (_ap(tmp_path) / "setup" / "cron-install.sh").exists()
    assert capsys.readouterr().out.splitlines() == ["wrote setup/cron-install.sh"]


def test_refresh_reports_missing_package_owned_file_but_not_operator_owned_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    ap = _ap(tmp_path)
    package_owned = ap / "setup" / "cron-install.sh"
    operator_owned = ap / "user-info" / "search-terms" / "keywords.md"
    package_owned.unlink()
    operator_owned.unlink()
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert package_owned.exists()
    assert operator_owned.exists()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines == ["wrote setup/cron-install.sh"]


def test_refresh_reseeds_missing_operator_owned_file_without_stdout_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    keywords = _ap(tmp_path) / "user-info" / "search-terms" / "keywords.md"
    keywords.unlink()
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert keywords.exists()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines == ["directory is current — no files changed"]


def test_refresh_unchanged_file_preserves_mtime(tmp_path: Path) -> None:
    init(tmp_path)
    cron = _ap(tmp_path) / "setup" / "cron.sh"
    mtime_before = cron.stat().st_mtime_ns

    init(tmp_path, refresh=True)

    assert cron.stat().st_mtime_ns == mtime_before


def test_rerun_skips_existing_package_owned_file_without_reading_bytes(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    cron = _ap(tmp_path) / "setup" / "cron.sh"
    original_mode = cron.stat().st_mode

    try:
        cron.chmod(0)
        init(tmp_path)
    finally:
        cron.chmod(original_mode)

    assert cron.read_text() == _setup_template_bytes("cron.sh").decode()


def test_refresh_preserves_operator_owned_file_without_reading_bytes(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    config = _ap(tmp_path) / "config.py"
    original_mode = config.stat().st_mode
    original = config.read_text()

    try:
        config.chmod(0)
        init(tmp_path, refresh=True)
    finally:
        config.chmod(original_mode)

    assert config.read_text() == original


def test_refresh_removed_lines_still_appear(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    (_ap(tmp_path) / "layout.py").write_text("# legacy\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert "removed layout.py" in capsys.readouterr().out


def test_fresh_init_creates_env_placeholder(tmp_path: Path) -> None:
    init(tmp_path)

    env_path = _ap(tmp_path) / ".env"
    assert env_path.exists()
    assert env_path.read_text() == "OPENCODE_GO_API_KEY=\n"
