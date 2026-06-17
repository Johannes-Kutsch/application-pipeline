from __future__ import annotations

import threading
from typing import Protocol

from application_pipeline.extracts.card_store import CardStore
from application_pipeline.llm import JudgeCandidate, MatchVerdict
from application_pipeline.parsers.types import PositionStub

__all__ = ["Pool"]


class Pool:
    """Run-scoped Pool admission state keyed by listing id.

    Admission ordering follows the existing pipeline flow:
    `add_judge_pending()` admits rediscovered matched listings from Parser Intake,
    and `add_matched()` admits newly classified matches from the Classify Stage.

    Storage invariants:
    - one thread-safe `listing_id -> PositionStub` entry per admitted listing
    - latest admission for a listing id replaces the previous stub
    - pool size counts admitted listing ids even if no Card exists yet
    """

    def __init__(self) -> None:
        self._stubs: dict[int, PositionStub] = {}
        self._lock = threading.Lock()

    def add_judge_pending(self, stub: PositionStub, listing_id: int) -> None:
        """Admit a Parser Intake rediscovery without exposing storage details."""
        self._store_stub(listing_id=listing_id, stub=stub)

    def add_matched(self, stub: PositionStub, listing_id: int) -> None:
        """Admit a Classify Stage match without exposing storage details."""
        self._store_stub(listing_id=listing_id, stub=stub)

    def judge_candidates(self, card_store: CardStore) -> list[JudgeCandidate]:
        with self._lock:
            stubs = dict(self._stubs)
        candidates = []
        for listing_id in stubs:
            card = card_store.get(listing_id)
            if card is None:
                continue
            candidates.append(
                JudgeCandidate(id=listing_id, header=card.header, summary=card.summary)
            )
        return candidates

    def apply_match_verdicts(
        self,
        verdicts: list[MatchVerdict],
        *,
        card_store: CardStore,
        daily_results_file: "DailyResultsFileWriter",
        dedup_store: "SelectedByJudgeRecorder",
    ) -> int:
        written = 0
        for verdict in sorted(verdicts, key=lambda item: item.rank):
            card = card_store.get(verdict.id)
            if card is None:
                continue
            with self._lock:
                stub = self._stubs.get(verdict.id)
            if stub is None:
                continue
            daily_results_file.commit(
                rank=verdict.rank,
                header=card.header,
                summary=card.summary,
                url=stub.url,
                body=card.body,
            )
            dedup_store.mark_selected_by_judge(verdict.id, stub)
            written += 1
        return written

    @property
    def pool_size(self) -> int:
        with self._lock:
            return len(self._stubs)

    def _store_stub(self, *, listing_id: int, stub: PositionStub) -> None:
        with self._lock:
            self._stubs[listing_id] = stub


class SelectedByJudgeRecorder(Protocol):
    def mark_selected_by_judge(
        self, key_or_listing_id: int, stub: PositionStub | None = None
    ) -> None: ...


class DailyResultsFileWriter(Protocol):
    def commit(
        self, *, rank: int, header: str, summary: str, url: str, body: str
    ) -> None: ...
