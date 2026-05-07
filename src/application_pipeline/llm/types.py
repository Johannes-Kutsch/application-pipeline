from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class LLMExtractorError(Exception):
    pass


class ExtractorUnreachableError(LLMExtractorError):
    pass


class MatchTier(str, Enum):
    green = "green"
    amber = "amber"
    red = "red"


@dataclass(frozen=True)
class RelevanceVerdict:
    in_domain: bool


@dataclass(frozen=True)
class MatchVerdict:
    tier: MatchTier
    matched: list[str]
    missing: list[str]
    summary: str


@runtime_checkable
class LLMExtractor(Protocol):
    def classify_relevance(
        self, language: str, title: str, raw_description: str
    ) -> RelevanceVerdict: ...

    def judge_match(self, language: str, raw_description: str) -> MatchVerdict: ...
