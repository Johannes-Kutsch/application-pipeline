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


def load_prompt_slots(triage_profile_dir: Path) -> TriageProfilePromptSlots:
    _check_legacy_files(triage_profile_dir)
    return TriageProfilePromptSlots(
        candidate_profile=_read_required_file(
            triage_profile_dir / "candidate-profile.md"
        ),
        gate_criteria=_read_required_file(triage_profile_dir / "gate-criteria.md"),
        skills=load_judge_text(triage_profile_dir / "skills.md"),
    )


def _check_legacy_files(triage_profile_dir: Path) -> None:
    legacy_domain_fit = triage_profile_dir / "domain-fit.md"
    if legacy_domain_fit.exists():
        raise _prompt_error(
            f"{legacy_domain_fit}: legacy file retired per ADR-0043; merge its "
            "in-scope / out-of-scope content into gate-criteria.md and delete the file."
        )

    legacy_self_description = triage_profile_dir / "self-description.md"
    if legacy_self_description.exists():
        raise _prompt_error(
            f"{legacy_self_description}: legacy filename retired; rename it to "
            "candidate-profile.md."
        )

    legacy_match_criteria = triage_profile_dir / "match-criteria.md"
    if legacy_match_criteria.exists():
        raise _prompt_error(
            f"{legacy_match_criteria}: legacy filename retired; rename it to "
            "gate-criteria.md."
        )


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
    "load_prompt_slots",
]
