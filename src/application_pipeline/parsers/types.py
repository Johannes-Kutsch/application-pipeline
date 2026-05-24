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

    def __post_init__(self) -> None:
        if not self.keyword:
            raise ValueError("keyword must be non-empty")


@dataclass(frozen=True)
class PositionStub:
    url: str
    title: str
    source: str
    company: str | None = None
    location: str | None = None
    posted_date: date | None = None
    _warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EnrichResult:
    stub: PositionStub
    body: str
    mode: Literal["native", "fallback"]


class EnrichFailedError(Exception):
    pass


@dataclass(frozen=True)
class NotServedQuery:
    """Sentinel emitted by parsers when a query location is not served.

    Consumed by the orchestrator to count skipped queries; never forwarded
    to classify or judge stages.
    """
