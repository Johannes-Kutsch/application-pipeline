from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class PositionStub:
    url: str
    title: str
    source: str
    company: str | None = None
    location: str | None = None
    language: str | None = None


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
