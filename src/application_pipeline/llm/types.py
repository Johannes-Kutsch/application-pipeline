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
    "rejected",
    "retryable",
    "expired",
]


@dataclass(frozen=True)
class MatchedListing:
    listing_id: int
    stub: "PositionStub"


@dataclass(frozen=True)
class AppliedClassifyItemOutcome:
    state: AppliedClassifyState
    event_matches: bool | None
    matched_listing: MatchedListing | None = None

    def __post_init__(self) -> None:
        valid_states = {"matched", "rejected", "retryable", "expired"}
        if self.state not in valid_states:
            raise ExtractorSchemaError(
                f"invalid applied classify state: {self.state!r}"
            )
        expected_event_matches = {
            "matched": True,
            "rejected": False,
            "retryable": None,
            "expired": None,
        }[self.state]
        if self.event_matches is not expected_event_matches:
            raise ExtractorSchemaError(
                "event_matches must match applied classify state"
            )
        if self.state == "matched" and self.matched_listing is None:
            raise ExtractorSchemaError(
                "matched outcomes must include matched listing data"
            )
        if self.state != "matched" and self.matched_listing is not None:
            raise ExtractorSchemaError(
                "only matched outcomes may include matched listing data"
            )


@dataclass(frozen=True)
class AppliedClassifyOutcome:
    items: list[AppliedClassifyItemOutcome]

    @property
    def matched_listings(self) -> list[tuple[int, "PositionStub"]]:
        return [
            (item.matched_listing.listing_id, item.matched_listing.stub)
            for item in self.items
            if item.matched_listing is not None
        ]

    @classmethod
    def from_verdicts(
        cls,
        items: list[tuple[int, "PositionStub", str]],
        verdicts: list[RelevanceVerdict | None],
    ) -> "AppliedClassifyOutcome":
        outcome_items: list[AppliedClassifyItemOutcome] = []
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
                outcome_items.append(
                    AppliedClassifyItemOutcome(
                        state="matched",
                        event_matches=True,
                        matched_listing=MatchedListing(
                            listing_id=listing_id, stub=stub
                        ),
                    )
                )
                continue
            outcome_items.append(
                AppliedClassifyItemOutcome(
                    state="rejected",
                    event_matches=False,
                )
            )
        return cls(items=outcome_items)


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
