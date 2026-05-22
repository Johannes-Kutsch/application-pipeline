import importlib.resources
import pathlib
import string
from dataclasses import dataclass

from .config import Config


class PromptError(Exception):
    pass


CLASSIFY_RELEVANCE_SLOTS: frozenset[str] = frozenset({"TITLE", "RAW_DESCRIPTION"})
JUDGE_MATCH_SLOTS: frozenset[str] = frozenset({"skills", "raw_description"})

_PACKAGE_CLASSIFY_SYSTEM_SLOTS: frozenset[str] = frozenset({"USER_INFO"})
_PACKAGE_CLASSIFY_USER_SLOTS: frozenset[str] = frozenset({"TITLE", "RAW_DESCRIPTION"})
_PACKAGE_JUDGE_SLOTS: frozenset[str] = frozenset(
    {"skills", "raw_description", "USER_INFO"}
)
_PACKAGE_JUDGE_TOP_N_SYSTEM_SLOTS: frozenset[str] = frozenset({"USER_INFO", "skills"})
_PACKAGE_JUDGE_TOP_N_USER_SLOTS: frozenset[str] = frozenset({"candidates"})

JUDGE_TOP_N_SYSTEM_SLOTS: frozenset[str] = frozenset({"skills"})
JUDGE_TOP_N_USER_SLOTS: frozenset[str] = frozenset({"candidates"})


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
class SplitPromptTemplate:
    system: PromptTemplate
    user: PromptTemplate

    def render_system(self, **slots: str) -> str:
        return self.system.render(**slots)

    def render_user(self, **slots: str) -> str:
        return self.user.render(**slots)


@dataclass(frozen=True)
class Prompts:
    classify_relevance: SplitPromptTemplate
    judge_match: PromptTemplate
    judge_top_n: SplitPromptTemplate


def load_prompts(config: Config) -> Prompts:
    triage_dir = config.user_info_dir / "triage-profile"
    self_desc = _read_user_info(triage_dir, "self-description.md")
    domain_fit = _read_user_info(triage_dir, "domain-fit.md")
    match_criteria = _read_user_info(triage_dir, "match-criteria.md")

    classify_user_info = f"<user-info>\n{self_desc}\n{domain_fit}\n</user-info>"
    judge_user_info = f"<user-info>\n{self_desc}\n{match_criteria}\n</user-info>"

    pkg = importlib.resources.files("application_pipeline.templates.prompts")
    classify = _load_split_template(
        pkg,
        "classify_relevance",
        _PACKAGE_CLASSIFY_SYSTEM_SLOTS,
        frozenset(),
        _PACKAGE_CLASSIFY_USER_SLOTS,
        CLASSIFY_RELEVANCE_SLOTS,
        classify_user_info,
    )
    judge = _load_package_template(
        pkg,
        "judge_match",
        _PACKAGE_JUDGE_SLOTS,
        JUDGE_MATCH_SLOTS,
        judge_user_info,
    )
    judge_top_n = _load_split_template(
        pkg,
        "judge_top_n",
        _PACKAGE_JUDGE_TOP_N_SYSTEM_SLOTS,
        JUDGE_TOP_N_SYSTEM_SLOTS,
        _PACKAGE_JUDGE_TOP_N_USER_SLOTS,
        JUDGE_TOP_N_USER_SLOTS,
        judge_user_info,
    )
    return Prompts(
        classify_relevance=classify, judge_match=judge, judge_top_n=judge_top_n
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


def _load_package_template(
    pkg: importlib.resources.abc.Traversable,
    call_site: str,
    package_slots: frozenset[str],
    render_slots: frozenset[str],
    user_info: str,
) -> PromptTemplate:
    filename = f"{call_site}.md"
    resource = pkg / filename
    try:
        raw = resource.read_text(encoding="utf-8-sig")
    except Exception as exc:
        raise PromptError(f"{filename}: {exc}") from exc

    _validate_slots(filename, raw, package_slots)

    escaped_user_info = user_info.replace("{", "{{").replace("}", "}}")
    template_text = raw.replace("{USER_INFO}", escaped_user_info)

    return PromptTemplate(template=template_text, expected_slots=render_slots)


def _load_split_template(
    pkg: importlib.resources.abc.Traversable,
    call_site: str,
    system_package_slots: frozenset[str],
    system_render_slots: frozenset[str],
    user_package_slots: frozenset[str],
    user_render_slots: frozenset[str],
    user_info: str,
) -> SplitPromptTemplate:
    system_filename = f"{call_site}.system.md"
    user_filename = f"{call_site}.user.md"

    system_resource = pkg / system_filename
    try:
        system_raw = system_resource.read_text(encoding="utf-8-sig")
    except Exception as exc:
        raise PromptError(f"{system_filename}: {exc}") from exc
    _validate_slots(system_filename, system_raw, system_package_slots)
    escaped_user_info = user_info.replace("{", "{{").replace("}", "}}")
    system_text = system_raw.replace("{USER_INFO}", escaped_user_info)

    user_resource = pkg / user_filename
    try:
        user_raw = user_resource.read_text(encoding="utf-8-sig")
    except Exception as exc:
        raise PromptError(f"{user_filename}: {exc}") from exc
    _validate_slots(user_filename, user_raw, user_package_slots)

    return SplitPromptTemplate(
        system=PromptTemplate(template=system_text, expected_slots=system_render_slots),
        user=PromptTemplate(template=user_raw, expected_slots=user_render_slots),
    )


def _validate_slots(filename: str, text: str, expected_slots: frozenset[str]) -> None:
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

    missing = expected_slots - found_slots
    unknown = found_slots - expected_slots
    if missing:
        raise PromptError(f"{filename}: missing slots: {missing!r}")
    if unknown:
        raise PromptError(f"{filename}: unknown slots: {unknown!r}")
