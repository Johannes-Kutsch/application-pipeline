from __future__ import annotations

import importlib.resources
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
    return (
        importlib.resources.files("application_pipeline.templates") / "user-info" / name
    ).read_bytes()


_USER_INFO_FILES = (
    "self-description.md",
    "domain-fit.md",
    "match-criteria.md",
)

_LATEX_USER_INFO_FILES = (
    "identity.tex",
    "contact.tex",
    "content_pool.tex",
    "profile.png",
    "signature.png",
)


def test_first_bootstrap_writes_both_files(tmp_path: Path) -> None:
    init(tmp_path)

    assert (tmp_path / "config.py").read_bytes() == _template_bytes("config.py")
    assert (tmp_path / "layout.py").read_bytes() == _template_bytes("layout.py")


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
    assert config.keywords
    assert config.skills
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
    for fname in _USER_INFO_FILES:
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
    for fname in _USER_INFO_FILES:
        assert f"wrote user-info/{fname}" in out


def test_seeded_config_and_user_info_load_prompts_without_error(tmp_path: Path) -> None:
    init(tmp_path)
    config = load(tmp_path / "config.py")

    prompts = load_prompts(config)

    from application_pipeline import PromptTemplate

    assert isinstance(prompts.classify_relevance, PromptTemplate)
    assert isinstance(prompts.judge_match, PromptTemplate)


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    init(tmp_path)
    first_contents = {
        p: (tmp_path / p).read_bytes() for p in ["config.py", "layout.py"]
    } | {
        f"user-info/{f}": (tmp_path / "user-info" / f).read_bytes()
        for f in _USER_INFO_FILES
    }

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
    for fname in _USER_INFO_FILES:
        assert f"skipped user-info/{fname} (already exists)" in out


def test_per_file_skip_leaves_existing_user_info_and_seeds_siblings(
    tmp_path: Path,
) -> None:
    (tmp_path / "user-info").mkdir()
    existing = tmp_path / "user-info" / "self-description.md"
    original_content = "# operator content\n"
    existing.write_text(original_content)

    init(tmp_path)

    assert existing.read_text() == original_content
    for fname in _USER_INFO_FILES:
        if fname != "self-description.md":
            assert (tmp_path / "user-info" / fname).exists(), (
                f"{fname} should be seeded"
            )


def test_per_file_skip_granular_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "user-info").mkdir()
    (tmp_path / "user-info" / "self-description.md").write_text("# custom\n")

    init(tmp_path)

    out = capsys.readouterr().out
    assert "skipped user-info/self-description.md (already exists)" in out
    for fname in _USER_INFO_FILES:
        if fname != "self-description.md":
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
        dest = tmp_path / "user-info" / fname
        assert dest.exists(), f"expected {dest} to be seeded by init"
        assert dest.read_bytes() == _user_info_template_bytes(fname)


def test_init_seeds_eight_files_under_user_info(tmp_path: Path) -> None:
    init(tmp_path)

    user_info = tmp_path / "user-info"
    seeded = {p.name for p in user_info.iterdir()}
    expected = set(_USER_INFO_FILES) | set(_LATEX_USER_INFO_FILES)
    assert seeded == expected


def test_rerun_skips_existing_latex_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    capsys.readouterr()

    init(tmp_path)

    out = capsys.readouterr().out
    for fname in _LATEX_USER_INFO_FILES:
        assert f"skipped user-info/{fname} (already exists)" in out


def test_rerun_preserves_latex_file_content(tmp_path: Path) -> None:
    init(tmp_path)
    identity_path = tmp_path / "user-info" / "identity.tex"
    original = identity_path.read_bytes()

    init(tmp_path)

    assert identity_path.read_bytes() == original


# --- LaTeX package (application_pipeline.latex) ---

_LATEX_PACKAGE_FILES = (
    "cv_template.tex",
    "moderncv.cls",
    "moderncvcolorblue.sty",
    "moderncvstylecasual.sty",
    "tweaklist.sty",
    "README.md",
)


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
    assert "30 0 * * *" in line
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


def test_main_refresh_flag_wired_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    from application_pipeline.__main__ import main

    # Seed initial files
    init(tmp_path)
    for fname in _SETUP_SCRIPTS:
        (tmp_path / "setup" / fname).write_text("# custom\n")
    capsys.readouterr()

    sys.argv = ["application-pipeline", "init", "--refresh", str(tmp_path)]
    main()

    out = capsys.readouterr().out
    assert "overwrote setup/cron.sh" in out


def test_refresh_console_output_distinguishes_overwrote_preserved_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init(tmp_path)
    # Modify all files
    for fname in _SETUP_SCRIPTS:
        (tmp_path / "setup" / fname).write_text("# custom\n")
    (tmp_path / "config.py").write_text("# custom\n")
    (tmp_path / "layout.py").write_text("# custom\n")
    (tmp_path / "user-info" / "self-description.md").write_text("# custom\n")
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
    assert "skipped user-info/self-description.md (preserved)" in out


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
    for fname in _USER_INFO_FILES:
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
    (tmp_path / "user-info" / "self-description.md").write_text(custom_user_info)

    init(tmp_path, refresh=True)

    # setup/ files must match package templates
    for fname in _SETUP_SCRIPTS:
        assert (tmp_path / "setup" / fname).read_bytes() == _setup_template_bytes(fname)
    # user files must retain user-modified content
    assert (tmp_path / "config.py").read_text() == custom_config
    assert (tmp_path / "layout.py").read_text() == custom_layout
    assert (
        tmp_path / "user-info" / "self-description.md"
    ).read_text() == custom_user_info
