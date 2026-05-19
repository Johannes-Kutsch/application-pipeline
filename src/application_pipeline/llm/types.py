from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


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


@dataclass(frozen=True)
class StructuredExtract:
    seniority: str | None
    work_model: Literal["remote", "hybrid", "on-site"] | None
    contract_type: Literal["permanent", "fixed-term", "freelance"] | None
    key_skills: list[str]
    key_responsibilities: list[str]
    must_have_requirements: list[str]
    notable_caveats: str


@dataclass(frozen=True)
class JudgeCandidate:
    id: str
    extract: "StructuredExtract"
    title: str
    company: str | None
    location: str | None


@dataclass(frozen=True)
class ClassifyItem:
    id: str
    title: str
    raw_description: str


@dataclass(frozen=True)
class RelevanceVerdict:
    in_domain: bool
    extract: StructuredExtract | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.in_domain, bool):
            raise ExtractorSchemaError(
                f"in_domain must be bool, got {type(self.in_domain).__name__}"
            )
        if self.in_domain and self.extract is None:
            raise ExtractorSchemaError(
                "extract must not be None when in_domain is True"
            )
        if not self.in_domain and self.extract is not None:
            raise ExtractorSchemaError("extract must be None when in_domain is False")


@dataclass(frozen=True)
class MatchVerdict:
    matched: list[str]
    missing: list[str]
    summary: str
    rank: int = 1
    id: str = ""

    def __post_init__(self) -> None:
        if len(self.matched) > 10 or len(self.missing) > 10:
            raise ExtractorSchemaError(
                "matched/missing must have at most 10 entries each"
            )
        if not (1 <= self.rank <= 5):
            raise ExtractorSchemaError(
                f"rank must be between 1 and 5, got {self.rank!r}"
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

    def judge_top_n(
        self, candidates: list[JudgeCandidate]
    ) -> tuple[list[MatchVerdict], CallUsage]: ...
