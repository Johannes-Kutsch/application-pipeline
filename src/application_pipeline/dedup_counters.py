from __future__ import annotations

import threading
from dataclasses import dataclass

from application_pipeline.dedup import RunScopedSeenResult
from application_pipeline.parser_log import RunLog
from application_pipeline.status_display import StatusDisplay


@dataclass(frozen=True)
class DedupSnapshot:
    dedup_url_hits: int = 0
    dedup_tuple_hits: int = 0
    dedup_run_hits: int = 0
    dedup_misses: int = 0
    judge_resumed: int = 0

    @property
    def skipped(self) -> int:
        return self.dedup_url_hits + self.dedup_tuple_hits + self.dedup_run_hits


class DedupCounters:
    def __init__(self, *, display: StatusDisplay, run_log: RunLog) -> None:
        self._display = display
        self._run_log = run_log
        self._lock = threading.Lock()
        self._dedup_url_hits = 0
        self._dedup_tuple_hits = 0
        self._dedup_run_hits = 0
        self._dedup_misses = 0
        self._judge_resumed = 0

    def register(self, order: int) -> None:
        self._display.register("pipeline_dedup", order=order, phase="running")

    def record(self, result: RunScopedSeenResult) -> None:
        with self._lock:
            if result == "url_hit":
                self._dedup_url_hits += 1
            elif result == "tuple_hit":
                self._dedup_tuple_hits += 1
            elif result == "run_hit":
                self._dedup_run_hits += 1
            elif result == "judge_pending":
                self._judge_resumed += 1
            else:  # miss
                self._dedup_misses += 1
            body = self._body()
        self._display.update_body("pipeline_dedup", body=body)

    def snapshot(self) -> DedupSnapshot:
        with self._lock:
            return DedupSnapshot(
                dedup_url_hits=self._dedup_url_hits,
                dedup_tuple_hits=self._dedup_tuple_hits,
                dedup_run_hits=self._dedup_run_hits,
                dedup_misses=self._dedup_misses,
                judge_resumed=self._judge_resumed,
            )

    def _body(self) -> str:
        return (
            f"url_hits={self._dedup_url_hits}"
            f" tuple_hits={self._dedup_tuple_hits}"
            f" run_hits={self._dedup_run_hits}"
            f" misses={self._dedup_misses}"
        )
