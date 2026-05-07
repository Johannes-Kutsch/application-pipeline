import pathlib
from dataclasses import dataclass
from typing import Literal

from .config import Config
from .user_settings import UserSettingsError


class PromptError(UserSettingsError):
    pass


CLASSIFY_RELEVANCE_SLOTS: frozenset[str] = frozenset({"title", "raw_description"})
JUDGE_MATCH_SLOTS: frozenset[str] = frozenset({"skills", "raw_description"})

_LANGS: tuple[Literal["de", "en"], ...] = ("de", "en")


@dataclass(frozen=True)
class Prompts:
    classify_relevance: dict[Literal["de", "en"], str]
    judge_match: dict[Literal["de", "en"], str]


def load_prompts(config: Config) -> Prompts:
    classify = {
        lang: _read(config.prompts_dir, "classify_relevance", lang) for lang in _LANGS
    }
    judge = {lang: _read(config.prompts_dir, "judge_match", lang) for lang in _LANGS}
    return Prompts(classify_relevance=classify, judge_match=judge)


def _read(prompts_dir: pathlib.Path, call_site: str, lang: str) -> str:
    filename = f"{call_site}.{lang}.md"
    path = prompts_dir / filename
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"{filename} could not be read from {path}: {exc}") from exc
    if not text.strip():
        raise PromptError(f"{filename} file is empty: {path}")
    return text
