import pathlib
import string
from dataclasses import dataclass
from typing import Literal

from .config import Config


class PromptError(Exception):
    pass


CLASSIFY_RELEVANCE_SLOTS: frozenset[str] = frozenset({"title", "raw_description"})
JUDGE_MATCH_SLOTS: frozenset[str] = frozenset({"skills", "raw_description"})

_LANGS: tuple[Literal["de", "en"], ...] = ("de", "en")


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
    classify_relevance: dict[Literal["de", "en"], PromptTemplate]
    judge_match: dict[Literal["de", "en"], PromptTemplate]


def load_prompts(config: Config) -> Prompts:
    classify = {
        lang: _read(
            config.prompts_dir, "classify_relevance", lang, CLASSIFY_RELEVANCE_SLOTS
        )
        for lang in _LANGS
    }
    judge = {
        lang: _read(config.prompts_dir, "judge_match", lang, JUDGE_MATCH_SLOTS)
        for lang in _LANGS
    }
    return Prompts(classify_relevance=classify, judge_match=judge)


def _read(
    prompts_dir: pathlib.Path,
    call_site: str,
    lang: str,
    expected_slots: frozenset[str],
) -> PromptTemplate:
    filename = f"{call_site}.{lang}.md"
    path = prompts_dir / filename
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PromptError(f"{path}: {exc}") from exc
    except OSError as exc:
        raise PromptError(f"{path}: {exc}") from exc
    if not text.strip():
        raise PromptError(f"{path}: file is empty")

    found_slots: set[str] = set()
    try:
        for _, field_name, format_spec, conversion in string.Formatter().parse(text):
            if field_name is None:
                continue
            if format_spec:
                raise PromptError(
                    f"{path}: format spec not allowed: {{{field_name}:{format_spec}}}"
                )
            if conversion:
                raise PromptError(
                    f"{path}: conversion flag not allowed: {{{field_name}!{conversion}}}"
                )
            found_slots.add(field_name)
    except ValueError as exc:
        raise PromptError(f"{path}: {exc}") from exc

    missing = expected_slots - found_slots
    unknown = found_slots - expected_slots
    if missing:
        raise PromptError(f"{path}: missing slots: {missing!r}")
    if unknown:
        raise PromptError(f"{path}: unknown slots: {unknown!r}")

    return PromptTemplate(template=text, expected_slots=expected_slots)
