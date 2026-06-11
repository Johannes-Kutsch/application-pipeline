import pathlib

import pytest

from application_pipeline import PromptError
from application_pipeline import triage_profile


@pytest.fixture
def triage_profile_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "user-info" / "triage-profile"
    path.mkdir(parents=True)
    (path / "candidate-profile.md").write_text("Candidate bullets\n")
    (path / "gate-criteria.md").write_text("Gate bullets\n")
    (path / "skills.md").write_text("- Python\n- SQL {always}\n")
    return path


def test_triage_profile_load_prompt_slots_returns_slot_values(
    triage_profile_dir: pathlib.Path,
) -> None:
    prompt_slots = triage_profile.load_prompt_slots(triage_profile_dir)

    assert triage_profile.TRIAGE_PROFILE_SLOTS == frozenset(
        {"CANDIDATE_PROFILE", "GATE_CRITERIA", "SKILLS"}
    )
    assert prompt_slots.candidate_profile == "Candidate bullets"
    assert prompt_slots.gate_criteria == "Gate bullets"
    assert prompt_slots.skills == "- Python\n- SQL"
    assert prompt_slots.as_dict() == {
        "CANDIDATE_PROFILE": "Candidate bullets",
        "GATE_CRITERIA": "Gate bullets",
        "SKILLS": "- Python\n- SQL",
    }


def test_triage_profile_load_prompt_slots_skills_missing_returns_empty_text(
    triage_profile_dir: pathlib.Path,
) -> None:
    (triage_profile_dir / "skills.md").unlink()

    prompt_slots = triage_profile.load_prompt_slots(triage_profile_dir)

    assert prompt_slots.skills == ""


def test_triage_profile_load_prompt_slots_raises_for_legacy_domain_fit_file(
    triage_profile_dir: pathlib.Path,
) -> None:
    legacy_file = triage_profile_dir / "domain-fit.md"
    legacy_file.write_text("legacy\n")

    with pytest.raises(
        PromptError,
        match=(
            rf"{legacy_file}: legacy filename retired; move its in-scope / "
            r"out-of-scope content into gate-criteria\.md and delete the file\."
        ),
    ):
        triage_profile.load_prompt_slots(triage_profile_dir)


def test_triage_profile_load_prompt_slots_raises_for_legacy_self_description_file(
    triage_profile_dir: pathlib.Path,
) -> None:
    legacy_file = triage_profile_dir / "self-description.md"
    legacy_file.write_text("legacy\n")

    with pytest.raises(
        PromptError,
        match=(
            rf"{legacy_file}: legacy filename retired; rename the file to "
            r"candidate-profile\.md\."
        ),
    ):
        triage_profile.load_prompt_slots(triage_profile_dir)


def test_triage_profile_load_prompt_slots_raises_for_legacy_match_criteria_file(
    triage_profile_dir: pathlib.Path,
) -> None:
    legacy_file = triage_profile_dir / "match-criteria.md"
    legacy_file.write_text("legacy\n")

    with pytest.raises(
        PromptError,
        match=(
            rf"{legacy_file}: legacy filename retired; rename the file to "
            r"gate-criteria\.md\."
        ),
    ):
        triage_profile.load_prompt_slots(triage_profile_dir)


@pytest.mark.parametrize(
    ("filename", "expected_text"),
    [
        ("candidate-profile.md", "candidate-profile.md"),
        ("gate-criteria.md", "gate-criteria.md"),
        ("domain-fit.md", "gate-criteria.md"),
        ("self-description.md", "candidate-profile.md"),
        ("match-criteria.md", "gate-criteria.md"),
    ],
)
def test_triage_profile_load_prompt_slots_raises_for_invalid_local_files(
    triage_profile_dir: pathlib.Path, filename: str, expected_text: str
) -> None:
    if filename in {"candidate-profile.md", "gate-criteria.md"}:
        (triage_profile_dir / filename).unlink()
    else:
        (triage_profile_dir / filename).write_text("legacy\n")

    with pytest.raises(PromptError) as exc_info:
        triage_profile.load_prompt_slots(triage_profile_dir)

    assert filename in str(exc_info.value)
    assert expected_text in str(exc_info.value)


@pytest.mark.parametrize("filename", ["candidate-profile.md", "gate-criteria.md"])
def test_triage_profile_load_prompt_slots_raises_for_empty_required_local_files(
    triage_profile_dir: pathlib.Path, filename: str
) -> None:
    (triage_profile_dir / filename).write_text("")

    with pytest.raises(PromptError) as exc_info:
        triage_profile.load_prompt_slots(triage_profile_dir)

    assert filename in str(exc_info.value)
