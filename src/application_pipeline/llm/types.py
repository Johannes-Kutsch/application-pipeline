from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from application_pipeline.parsers.types import PositionStub


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
        self,
        message: str,
        *,
        returncode: int | None = None,
        stderr: str = "",
        prompt: str | None = None,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
        self.prompt = prompt
        self.raw_response = raw_response


class ExtractorSchemaError(ExtractorError):
    pass


class ExtractorBatchMalformedError(ExtractorError):
    pass


class ExtractorMalformedError(ExtractorError):
    def __init__(
        self,
        message: str,
        *,
        prompt: str | None = None,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        self.prompt = prompt
        self.raw_response = raw_response


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
class RelevanceVerdict:
    matches: bool
    header: str | None = None
    summary: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.matches, bool):
            raise ExtractorSchemaError(
                f"matches must be bool, got {type(self.matches).__name__}"
            )
        if self.matches and (self.header is None or self.summary is None):
            raise ExtractorSchemaError(
                "header and summary must not be None when matches is True"
            )
        if not self.matches and (self.header is not None or self.summary is not None):
            raise ExtractorSchemaError(
                "header and summary must be None when matches is False"
            )


AppliedClassifyState = Literal[
    "matched",
    "out_of_domain",
    "retryable",
    "expired",
]


@dataclass(frozen=True)
class AppliedClassifyItemOutcome:
    state: AppliedClassifyState
    event_matches: bool | None

    def __post_init__(self) -> None:
        valid_states = {"matched", "out_of_domain", "retryable", "expired"}
        if self.state not in valid_states:
            raise ExtractorSchemaError(
                f"invalid applied classify state: {self.state!r}"
            )
        expected_event_matches = {
            "matched": True,
            "out_of_domain": False,
            "retryable": None,
            "expired": None,
        }[self.state]
        if self.event_matches is not expected_event_matches:
            raise ExtractorSchemaError(
                "event_matches must match applied classify state"
            )


@dataclass(frozen=True)
class AppliedClassifyOutcome:
    items: list[AppliedClassifyItemOutcome]
    matched_listings: list[tuple[int, "PositionStub"]]

    @classmethod
    def from_verdicts(
        cls,
        items: list[tuple[int, "PositionStub", str]],
        verdicts: list[RelevanceVerdict | None],
    ) -> "AppliedClassifyOutcome":
        outcome_items: list[AppliedClassifyItemOutcome] = []
        matched_listings: list[tuple[int, "PositionStub"]] = []
        for (listing_id, stub, _), verdict in zip(items, verdicts):
            if verdict is None:
                outcome_items.append(
                    AppliedClassifyItemOutcome(
                        state="retryable",
                        event_matches=None,
                    )
                )
                continue
            if verdict.matches:
                matched_listings.append((listing_id, stub))
                outcome_items.append(
                    AppliedClassifyItemOutcome(
                        state="matched",
                        event_matches=True,
                    )
                )
                continue
            outcome_items.append(
                AppliedClassifyItemOutcome(
                    state="out_of_domain",
                    event_matches=False,
                )
            )
        return cls(items=outcome_items, matched_listings=matched_listings)


@dataclass(frozen=True)
class JudgeCandidate:
    id: int
    header: str
    summary: str


@dataclass(frozen=True)
class MatchVerdict:
    id: int
    rank: int

    def __post_init__(self) -> None:
        if not (1 <= self.rank <= 5):
            raise ExtractorSchemaError(
                f"rank must be between 1 and 5, got {self.rank!r}"
            )
