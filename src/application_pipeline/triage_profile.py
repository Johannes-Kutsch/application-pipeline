from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .triage_skills import load_judge_text

TRIAGE_PROFILE_SLOTS: frozenset[str] = frozenset(
    {"CANDIDATE_PROFILE", "GATE_CRITERIA", "SKILLS"}
)


@dataclass(frozen=True)
class TriageProfilePromptSlots:
    candidate_profile: str
    gate_criteria: str
    skills: str

    def as_dict(self) -> dict[str, str]:
        return {
            "CANDIDATE_PROFILE": self.candidate_profile,
            "GATE_CRITERIA": self.gate_criteria,
            "SKILLS": self.skills,
        }


LEGACY_TRIAGE_PROFILE_FILES: tuple[tuple[str, str], ...] = (
    (
        "domain-fit.md",
        "legacy file retired per ADR-0043; merge its in-scope / out-of-scope "
        "content into gate-criteria.md and delete the file.",
    ),
    (
        "self-description.md",
        "legacy filename retired; rename it to candidate-profile.md.",
    ),
    (
        "match-criteria.md",
        "legacy filename retired; rename it to gate-criteria.md.",
    ),
)


def load_prompt_slots(triage_profile_dir: Path) -> TriageProfilePromptSlots:
    _check_legacy_files(triage_profile_dir)
    return TriageProfilePromptSlots(
        candidate_profile=_read_required_file(
            triage_profile_dir / "candidate-profile.md"
        ),
        gate_criteria=_read_required_file(triage_profile_dir / "gate-criteria.md"),
        skills=load_skills_slot(triage_profile_dir),
    )


def load_skills_slot(triage_profile_dir: Path) -> str:
    return load_judge_text(triage_profile_dir / "skills.md")


def _check_legacy_files(triage_profile_dir: Path) -> None:
    for filename, message in LEGACY_TRIAGE_PROFILE_FILES:
        legacy_path = triage_profile_dir / filename
        if legacy_path.exists():
            raise _prompt_error(f"{legacy_path}: {message}")


def _read_required_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise _prompt_error(f"{path}: {exc}") from exc
    if not text.strip():
        raise _prompt_error(f"{path}: file is empty")
    return text.rstrip("\n")


def _prompt_error(*args: object) -> Exception:
    from .prompts import PromptError

    return PromptError(*args)


__all__ = [
    "TRIAGE_PROFILE_SLOTS",
    "TriageProfilePromptSlots",
    "load_skills_slot",
    "load_prompt_slots",
]
