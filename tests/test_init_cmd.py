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


def _skill_frontmatter(text: str) -> tuple[str, str]:
    match = re.match(
        r"^---\nname: (?P<name>[^\n]+)\ndescription: (?P<description>[^\n]+)\n---\n",
        text,
    )
    assert match is not None
    return match.group("name"), match.group("description")


def _codex_template_bytes(rel: str) -> bytes:
    node = importlib.resources.files("application_pipeline.templates") / "codex"
    for part in rel.split("/"):
        node = node / part
    return node.read_bytes()


def _codex_template_text(rel: str) -> str:
    return _codex_template_bytes(rel).decode()


def _front_matter_field(text: str, field: str) -> str:
    match = re.search(rf"^{field}: .+$", text, flags=re.MULTILINE)
    assert match is not None
    return match.group(0)


def _analyse_listing_step_2(text: str) -> str:
    match = re.search(r"^2\. \*\*(?P<step>.+)$", text, flags=re.MULTILINE)
    assert match is not None
    return match.group("step")


def _assert_cover_strategy_contract(text: str) -> None:
    match = re.search(
        r"^### Cover strategy\n(?P<body>.*?)(?=^# Tailoring hooks$)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None

    body = match.group("body")
    assert "**Lead hook:**" in body
    assert "**Warum dieser Hook:**" in body
    assert "**Supporting hooks:**" in body
    assert "**Reserve hooks:**" in body
    assert "Resume, Skills oder spätere Iteration" in body
    assert body.count("`none`") == 2


def _assert_analysis_cover_sections(text: str) -> None:
    match = re.search(
        r"^# Cover sections\n(?P<body>.*?)(?=^# Tailoring hooks$)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None

    body = match.group("body")
    assert "## intro" in body
    assert "## bridge" in body
    assert "## evidence" in body
    assert "## closing" in body
    assert body.index("## intro") < body.index("## bridge")
    assert body.index("## bridge") < body.index("## evidence")
    assert body.index("## evidence") < body.index("## closing")


def _assert_analysis_cover_sections_preserve_semantics(text: str) -> None:
    match = re.search(
        r"^# Cover sections\n(?P<body>.*?)(?=^# Tailoring hooks$)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None

    body = match.group("body")
    assert "Why-apply" in body
    assert "Lead hook" in body
    assert "Supporting hooks" in body
    assert "Resonance" in body
    assert "Capability" in body
    assert "Anekdoten" in body


def _assert_write_cv_cover_strategy_usage(text: str) -> None:
    assert (
        'analysis.md` — neutraler Listing-Summary + „Why apply"-Bullets + '
        "`Cover strategy` (ein Lead-Hook mit Supporting/Reserve-Hooks)"
    ) in text
    assert "Nutze den `Lead hook` aus der `Cover strategy` in `analysis.md`" in text
    assert "`Supporting hooks` dürfen ihn stützen" in text
    assert (
        "`Reserve hooks` bleiben für Resume, Skills oder spätere Iteration liegen"
        in text
    )


def _assert_write_cv_reads_cover_sections_directly(text: str) -> None:
    assert "`Cover sections` (`intro`, `bridge`, `evidence`, `closing`)" in text
    assert "direkter Handoff" in text
    assert "`cover_intro` aus `intro`" in text
    assert "`cover_pivot` aus `bridge`" in text
    assert "`cover_fit` aus `evidence`" in text
    assert "`cover_closing` aus `closing`" in text


def _assert_write_cv_cover_strategy_contract(text: str) -> None:
    _assert_write_cv_cover_strategy_usage(text)
    assert "application-pipeline/user-info/cv/cover-patterns.md" in text
    assert (
        "`positive-exemplars.md` und `writing-style.md` werden fuer die vier "
        "Cover-Prosa-Slots bewusst **nicht** gelesen"
    ) in text
    assert "persönlicher, listingspezifischer Resonanz-Hook" in text
    assert "keine Mehrfach-Nennung von Projektnamen im Opener" in text
    assert "ein dominanter Capability-Arc" in text
    assert "höchstens zwei Evidence-Anchors" in text
    assert "Octofox, pycastle und application-pipeline" in text
    assert "selektierbare Evidence-Anchors" in text
    assert "nicht feste Absatz-Slots" in text
    assert (
        "Weitere Projekte bleiben für Resume-Slots, Skills-Block oder spätere Iteration"
        in text
    )


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


def test_init_seeds_cover_patterns_template(tmp_path: Path) -> None:
    init(tmp_path)

    cover_patterns = (
        _ap(tmp_path) / "user-info" / "cv" / "cover-patterns.md"
    ).read_text()

    assert "# Cover Paragraph Patterns" in cover_patterns
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
    for rel in ("analyse-listing.md", "write-cv.md"):
        text = (shared_root / rel).read_text()
        assert "../_shared/" not in text
        assert "_shared/" in text


def test_fresh_init_seeds_write_cv_with_cover_strategy_routing_contract(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    text = (_ap(tmp_path) / "agent-skills" / "write-cv.md").read_text()

    assert "Strategie-Form, Inhalt/Bogen/Beleg pro Slot" in text
    assert "Bullet in `cv/writing-style.md` Sektion `## Cover-Strategie`" in text
    assert "nur wenn es ein positives Vorbild" in text
    assert "realen handgeschriebenen Brief" in text
    assert "keine Negativ-Exemplare in `cv/writing-style.md`" in text
    assert "abstrahiere zur Regel" in text
    assert "verwirf den Beispiel-Satz" in text


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


def test_refresh_overwrites_shared_write_cv_with_cover_strategy_routing_contract(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    skill_body = _ap(tmp_path) / "agent-skills" / "write-cv.md"
    skill_body.write_text("# tampered\n")

    init(tmp_path, refresh=True)

    text = skill_body.read_text()
    assert "nur wenn es ein positives Vorbild" in text
    assert "realen handgeschriebenen Brief" in text
    assert "schlechter KI-Draft" in text


def test_analyse_listing_template_defines_primary_cover_strategy_arc() -> None:
    text = _agent_skill_template_bytes("analyse-listing.md").decode()

    _assert_cover_strategy_contract(text)


def test_fresh_init_seeds_analyse_listing_primary_cover_strategy_arc(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    text = (_ap(tmp_path) / "agent-skills" / "analyse-listing.md").read_text()
    _assert_cover_strategy_contract(text)


def test_analyse_listing_template_defines_four_explicit_cover_sections() -> None:
    text = _agent_skill_template_bytes("analyse-listing.md").decode()

    _assert_analysis_cover_sections(text)


def test_analyse_listing_template_sorts_existing_cover_semantics_into_sections() -> (
    None
):
    text = _agent_skill_template_bytes("analyse-listing.md").decode()

    _assert_analysis_cover_sections_preserve_semantics(text)


def test_write_cv_template_reads_cover_strategy_from_analysis() -> None:
    text = _agent_skill_template_bytes("write-cv.md").decode()

    _assert_write_cv_cover_strategy_usage(text)


def test_write_cv_template_reads_cover_sections_as_direct_handoff() -> None:
    text = _agent_skill_template_bytes("write-cv.md").decode()

    _assert_write_cv_reads_cover_sections_directly(text)


def test_write_cv_template_follows_cover_strategy_contract() -> None:
    text = _agent_skill_template_bytes("write-cv.md").decode()

    _assert_write_cv_cover_strategy_contract(text)


def test_write_cv_template_follows_interactive_cover_drafting_contract() -> None:
    text = _agent_skill_template_bytes("write-cv.md").decode()

    # Opening is automatic; cover prose slots are interactive
    assert "Kein User-Loop fuer `opening`" in text
    assert "`cv.tex` erst schreiben, wenn alle vier Slots bestaetigt sind" in text

    # Per-slot loop: one pattern match offer, then three typed alternatives
    assert "Cover Paragraph Pattern" in text
    assert "genau **einen** Vorschlag als **Cover Paragraph Pattern**-Match" in text
    assert "genau **drei** Alternativen" in text
    assert "anderen** `argument_type`" in text
    assert "voll ausformulierter Absatz gezeigt, nicht als Outline" in text

    # Pattern save rules: significant new only, during main loop or on explicit request
    assert "signifikant neu" in text
    assert "auf expliziten User-Wunsch" in text

    # cover-patterns.md exclusions during shortening loop
    assert (
        "Post-Build-Shortening-Loop schreibt **nie** nach `cover-patterns.md`" in text
    )
    assert (
        "Resume-Overflow bleibt automatisch; nur Cover-Prosa wird interaktiv verkuerzt"
        in text
    )

    # Interactive Cover Shortening shows full variants
    assert "Interactive Cover Shortening" in text
    assert "verkuerzten Varianten **vollstaendig** in Prosa" in text

    # Success report must keep the user in the same /write-cv run
    assert "bleib im selben `/write-cv`-Run" in text
    assert "Resident-Loop" in text
    assert "Compile-Fehler mitten in der Iteration" in text
    assert "## Exit" in text


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


def test_refresh_removes_retired_iterate_cv_files_from_all_buckets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    iterate_cv_md = _ap(tmp_path) / "agent-skills" / "iterate-cv.md"
    iterate_cv_md.write_text("# old body\n")
    claude_skill = _claude(tmp_path) / "skills" / "iterate-cv" / "SKILL.md"
    claude_skill.parent.mkdir(parents=True, exist_ok=True)
    claude_skill.write_text("# old claude\n")
    codex_skill = _codex(tmp_path) / "skills" / "iterate-cv" / "SKILL.md"
    codex_skill.parent.mkdir(parents=True, exist_ok=True)
    codex_skill.write_text("# old codex\n")
    capsys.readouterr()

    init(tmp_path, refresh=True)

    assert not iterate_cv_md.exists()
    assert not claude_skill.exists()
    assert not codex_skill.exists()
    out = capsys.readouterr().out
    assert "removed agent-skills/iterate-cv.md" in out
    assert "removed skills/iterate-cv/SKILL.md" in out


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


def test_refresh_is_silent_when_retired_iterate_cv_files_absent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path, refresh=True)

    out = capsys.readouterr().out
    assert "iterate-cv" not in out


# --- .codex/skills/ seeding (issue #689) ---


def test_fresh_init_seeds_codex_skill_wrappers_with_claude_metadata(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    codex_skills = _codex(tmp_path) / "skills"
    assert codex_skills.is_dir()
    for d in ("analyse-listing", "write-cv"):
        wrapper = codex_skills / d / "SKILL.md"
        assert wrapper.exists(), f"{d}/SKILL.md missing"
        assert wrapper.read_bytes() == _codex_template_bytes(f"skills/{d}/SKILL.md")

        codex_text = wrapper.read_text()
        claude_text = _claude_template_text(f"skills/{d}/SKILL.md")
        assert _front_matter_field(codex_text, "name") == _front_matter_field(
            claude_text, "name"
        )
        assert _front_matter_field(codex_text, "description") == _front_matter_field(
            claude_text, "description"
        )
        assert f"../../../application-pipeline/agent-skills/{d}.md" in codex_text

    assert not (_ap(tmp_path) / ".codex").exists()


def test_refresh_overwrites_package_owned_codex_skill_files(tmp_path: Path) -> None:
    init(tmp_path)
    skill_file = _codex(tmp_path) / "skills" / "analyse-listing" / "SKILL.md"
    skill_file.write_text("# tampered\n")

    init(tmp_path, refresh=True)

    assert skill_file.read_bytes() == _codex_template_bytes(
        "skills/analyse-listing/SKILL.md"
    )


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


# --- .claude/skills/ seeding (ADR-0044) ---


def test_fresh_init_seeds_claude_skills(tmp_path: Path) -> None:
    init(tmp_path)

    claude_skills = _claude(tmp_path) / "skills"
    assert claude_skills.is_dir()
    assert not (claude_skills / "_shared").exists()
    for d in ("analyse-listing", "write-cv"):
        assert (claude_skills / d).is_dir(), f"{d} missing"


def test_fresh_init_seeds_claude_wrappers_that_delegate_to_shared_bodies(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    expected = {
        "analyse-listing": (
            "Grills the user about why they want to apply to a specific listing and writes the conclusion into a per-listing application folder. Always one listing per session. Runs when the user types /analyse-listing.",
            "../../../application-pipeline/agent-skills/analyse-listing.md",
        ),
        "write-cv": (
            "Generates a tailored cv.tex (CV Slot-Map) plus cover/resume/combined PDFs for a listing previously analysed by /analyse-listing, then stays in the same resident edit loop for follow-up cv.tex, analysis.md, and triage-profile feedback until the user signals done. Calls `application-pipeline compile-cv` and iteratively strips content until cover ≤ 1 page and resume ≤ 2 pages. Runs when the user types /write-cv.",
            "../../../application-pipeline/agent-skills/write-cv.md",
        ),
    }

    for skill, (description, body_path) in expected.items():
        text = (_claude(tmp_path) / "skills" / skill / "SKILL.md").read_text()
        assert _skill_frontmatter(text) == (skill, description)
        assert body_path in text


def test_fresh_init_seeds_known_skill_files_with_template_content(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    claude_skills = _claude(tmp_path) / "skills"
    expected_files = [
        "analyse-listing/SKILL.md",
        "write-cv/SKILL.md",
    ]
    for rel in expected_files:
        dest = claude_skills / rel
        assert dest.exists(), f"expected {rel} to be seeded"
        assert dest.read_bytes() == _claude_template_bytes(f"skills/{rel}")


def test_refresh_overwrites_package_owned_skill_files(tmp_path: Path) -> None:
    init(tmp_path)
    skill_file = _claude(tmp_path) / "skills" / "write-cv" / "SKILL.md"
    skill_file.write_text("# tampered\n")

    init(tmp_path, refresh=True)

    assert skill_file.read_bytes() == _claude_template_bytes("skills/write-cv/SKILL.md")


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

    assert skill_file.read_bytes() == _claude_template_bytes("skills/write-cv/SKILL.md")
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
