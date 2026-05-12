from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class City:
    name: str


@dataclass(frozen=True)
class Remote:
    pass


Location = City | Remote


@dataclass
class ParserQuery:
    keyword: str
    location: Location
    max_results: int

    def __post_init__(self) -> None:
        if not self.keyword:
            raise ValueError("keyword must be non-empty")
        if self.max_results <= 0:
            raise ValueError("max_results must be positive")


@dataclass(frozen=True)
class PositionStub:
    url: str
    title: str
    source: str
    company: str | None = None
    location: str | None = None
    language: Literal["de", "en"] | None = None
    posted_date: date | None = None
    _warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotServedQuery:
    """Sentinel emitted by parsers when a query location is not served.

    Consumed by the orchestrator to count skipped queries; never forwarded
    to classify or judge stages.
    """


@dataclass(frozen=True)
class ExternalRedirect:
    stub: PositionStub
    outbound_url: str


@dataclass(frozen=True)
class Position:
    stub: PositionStub
    raw_description: str
    salary: str | None = None
    contract_type: Literal["permanent", "fixed-term", "freelance"] | None = None
    employment_type: Literal["full-time", "part-time", "internship"] | None = None
    work_model: Literal["remote", "hybrid", "on-site"] | None = None
    posted_date: date | None = None
    deadline: date | None = None
    _warnings: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return self.stub.title
