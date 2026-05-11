from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class ExtractorError(Exception):
    pass


class ExtractorUnreachableError(ExtractorError):
    pass


class ExtractorMalformedJSONError(ExtractorError):
    pass


class ExtractorSchemaError(ExtractorError):
    pass


class MatchTier(str, Enum):
    green = "green"
    amber = "amber"
    red = "red"


@dataclass(frozen=True)
class RelevanceVerdict:
    in_domain: bool

    def __post_init__(self) -> None:
        if not isinstance(self.in_domain, bool):
            raise ExtractorSchemaError(
                f"in_domain must be bool, got {type(self.in_domain).__name__}"
            )


@dataclass(frozen=True)
class MatchVerdict:
    tier: MatchTier
    matched: list[str]
    missing: list[str]
    summary: str

    def __post_init__(self) -> None:
        if not isinstance(self.tier, MatchTier):
            raise ExtractorSchemaError(f"tier must be a MatchTier, got {self.tier!r}")
        if len(self.matched) > 10 or len(self.missing) > 10:
            raise ExtractorSchemaError(
                "matched/missing must have at most 10 entries each"
            )
        for item in (*self.matched, *self.missing):
            if len(item) > 80:
                raise ExtractorSchemaError(f"entry exceeds 80 chars: {item!r}")
        if len(self.summary) > 600:
            raise ExtractorSchemaError(
                f"summary exceeds 600 chars (got {len(self.summary)})"
            )


@runtime_checkable
class LLMExtractor(Protocol):
    def classify_relevance(
        self, language: str, title: str, raw_description: str
    ) -> RelevanceVerdict: ...

    def judge_match(self, language: str, raw_description: str) -> MatchVerdict: ...

    def prewarm(self) -> None: ...
