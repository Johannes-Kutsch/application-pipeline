from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from application_pipeline.parsers.types import PositionStub

ListingId = int
RawDescription = str
ParserIdentity = str


@dataclass(frozen=True)
class ClassifyReadySubmission:
    listing_id: ListingId
    stub: PositionStub
    raw_description: RawDescription


@dataclass(frozen=True)
class ClassifyRequest:
    submission: ClassifyReadySubmission
    parser_id: ParserIdentity


@runtime_checkable
class ClassifyStageHandoff(Protocol):
    def submit(self, request: ClassifyRequest) -> None: ...
