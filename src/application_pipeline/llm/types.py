from dataclasses import dataclass
from datetime import date


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


class ExtractorMalformedError(ExtractorError):
    pass


@dataclass(frozen=True)
class ClassifyItem:
    title: str
    raw_description: str
    company: str | None = None
    location: str | None = None
    posted_date: date | None = None


@dataclass(frozen=True)
class CallUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    duration_s: float


@dataclass(frozen=True)
class RelevanceVerdictV2:
    in_domain: bool
    header: str | None = None
    summary: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.in_domain, bool):
            raise ExtractorSchemaError(
                f"in_domain must be bool, got {type(self.in_domain).__name__}"
            )
        if self.in_domain and (self.header is None or self.summary is None):
            raise ExtractorSchemaError(
                "header and summary must not be None when in_domain is True"
            )
        if not self.in_domain and (self.header is not None or self.summary is not None):
            raise ExtractorSchemaError(
                "header and summary must be None when in_domain is False"
            )


@dataclass(frozen=True)
class JudgeCandidateV2:
    id: str
    header: str
    summary: str


@dataclass(frozen=True)
class MatchVerdictV2:
    id: str
    rank: int

    def __post_init__(self) -> None:
        if not (1 <= self.rank <= 5):
            raise ExtractorSchemaError(
                f"rank must be between 1 and 5, got {self.rank!r}"
            )
