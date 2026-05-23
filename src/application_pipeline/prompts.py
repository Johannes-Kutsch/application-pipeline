import importlib.resources
import pathlib
import string
from dataclasses import dataclass

from .config import Config
from .search_terms.types import SearchTerms


class PromptError(Exception):
    pass


CLASSIFY_RELEVANCE_V2_SLOTS: frozenset[str] = frozenset(
    {"LISTING_BULLETS", "RAW_DESCRIPTION"}
)
JUDGE_TOP_N_V2_SLOTS: frozenset[str] = frozenset({"CANDIDATES"})

_PROFILE_SLOTS: frozenset[str] = frozenset(
    {"SELF_DESCRIPTION", "MATCH_CRITERIA", "SKILLS"}
)


@dataclass(frozen=True)
class PromptTemplate:
    template: str
    expected_slots: frozenset[str]

    def render(self, **slots: str) -> str:
        given = frozenset(slots)
        missing = self.expected_slots - given
        unknown = given - self.expected_slots
        if missing:
            raise PromptError(f"missing slots: {missing!r}")
        if unknown:
            raise PromptError(f"unknown slots: {unknown!r}")
        return self.template.format(**slots)


@dataclass(frozen=True)
class Prompts:
    classify_relevance_v2: PromptTemplate
    judge_top_n_v2: PromptTemplate


def load_prompts(config: Config, search_terms: SearchTerms) -> Prompts:
    triage_dir = config.user_info_dir / "triage-profile"
    legacy_domain_fit = triage_dir / "domain-fit.md"
    if legacy_domain_fit.exists():
        raise PromptError(
            f"{legacy_domain_fit}: legacy file retired per ADR-0043; merge its "
            "in-scope / out-of-scope content into match-criteria.md and delete the file."
        )

    profile_values: dict[str, str] = {
        "SELF_DESCRIPTION": _read_user_info(triage_dir, "self-description.md"),
        "MATCH_CRITERIA": _read_user_info(triage_dir, "match-criteria.md"),
        "SKILLS": "\n".join(f"- {s}" for s in search_terms.skills),
    }

    pkg = importlib.resources.files("application_pipeline.templates.prompts")
    classify_v2 = _load_template(
        pkg,
        "classify_relevance_v2",
        CLASSIFY_RELEVANCE_V2_SLOTS,
        profile_values,
    )
    judge_top_n_v2 = _load_template(
        pkg,
        "judge_top_n_v2",
        JUDGE_TOP_N_V2_SLOTS,
        profile_values,
    )
    return Prompts(
        classify_relevance_v2=classify_v2,
        judge_top_n_v2=judge_top_n_v2,
    )


def _read_user_info(user_info_dir: pathlib.Path, filename: str) -> str:
    path = user_info_dir / filename
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise PromptError(f"{path}: {exc}") from exc
    if not text.strip():
        raise PromptError(f"{path}: file is empty")
    return text.rstrip("\n")


def _load_template(
    pkg: importlib.resources.abc.Traversable,
    call_site: str,
    required_data_slots: frozenset[str],
    profile_values: dict[str, str],
) -> PromptTemplate:
    filename = f"{call_site}.md"
    resource = pkg / filename
    try:
        raw = resource.read_text(encoding="utf-8-sig")
    except Exception as exc:
        raise PromptError(f"{filename}: {exc}") from exc

    found = _parse_slots(filename, raw)
    allowed = required_data_slots | _PROFILE_SLOTS
    missing = required_data_slots - found
    unknown = found - allowed
    if missing:
        raise PromptError(f"{filename}: missing required data slots: {missing!r}")
    if unknown:
        raise PromptError(f"{filename}: unknown slots: {unknown!r}")

    text = raw
    for slot in _PROFILE_SLOTS & found:
        escaped = profile_values[slot].replace("{", "{{").replace("}", "}}")
        text = text.replace("{" + slot + "}", escaped)
    return PromptTemplate(template=text, expected_slots=required_data_slots)


def _parse_slots(filename: str, text: str) -> frozenset[str]:
    found_slots: set[str] = set()
    try:
        for _, field_name, format_spec, conversion in string.Formatter().parse(text):
            if field_name is None:
                continue
            if format_spec:
                raise PromptError(
                    f"{filename}: format spec not allowed: {{{field_name}:{format_spec}}}"
                )
            if conversion:
                raise PromptError(
                    f"{filename}: conversion flag not allowed: {{{field_name}!{conversion}}}"
                )
            found_slots.add(field_name)
    except ValueError as exc:
        raise PromptError(f"{filename}: {exc}") from exc
    return frozenset(found_slots)
