from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class ExtractorError(Exception):
    pass


class ExtractorUnreachableError(ExtractorError):
    def __init__(
        self, message: str, *, returncode: int | None = None, stderr: str = ""
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class ExtractorMalformedJSONError(ExtractorError):
    def __init__(
        self, message: str, *, returncode: int | None = None, stderr: str = ""
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class ExtractorSchemaError(ExtractorError):
    pass


class ExtractorBatchMalformedError(ExtractorError):
    pass


class MatchTier(str, Enum):
    green = "green"
    amber = "amber"
    red = "red"


@dataclass(frozen=True)
class ClassifyItem:
    id: str
    title: str
    raw_description: str


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


@dataclass(frozen=True)
class CallUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    duration_s: float


@runtime_checkable
class LLMExtractor(Protocol):
    def classify_relevance_batch(
        self, items: list[ClassifyItem]
    ) -> tuple[list[RelevanceVerdict], CallUsage]: ...

    def judge_match(
        self, raw_description: str, *, stub_url: str = ""
    ) -> tuple[MatchVerdict, CallUsage]: ...

    def prewarm(self) -> None: ...
