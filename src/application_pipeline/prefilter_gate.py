from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal, Protocol

from application_pipeline.parser_log import RunLog
from application_pipeline.status_display import StatusDisplay
from application_pipeline.text import normalize


class _Stub(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def source(self) -> str: ...

    @property
    def company(self) -> str | None: ...

    @property
    def title(self) -> str | None: ...

    @property
    def location(self) -> str | None: ...


class _DedupStore(Protocol):
    def mark_out_of_domain(self, key: _Stub) -> None: ...


@dataclass(frozen=True)
class _PreFilterVerdict:
    passes: bool
    reason: Literal["passed", "blacklist_drop"]
    blacklist_matches: tuple[str, ...]


def _precompute_blacklist(negative_keywords: list[str]) -> list[str]:
    return [n for k in negative_keywords if (n := normalize(k))]


def _evaluate(stub: _Stub, blacklist: list[str]) -> _PreFilterVerdict:
    # A stub whose title is None is treated as the empty string for blacklist matching.
    title_hay = normalize(stub.title or "") or ""
    blacklist_matches = tuple(k for k in blacklist if k in title_hay)
    passes = not blacklist_matches
    return _PreFilterVerdict(
        passes=passes,
        reason="passed" if passes else "blacklist_drop",
        blacklist_matches=blacklist_matches,
    )


def _format_keyword_hits(terms: list[str], counts: dict[str, int]) -> str:
    return " ".join(f"{term}={counts[term]}" for term in terms)


def _format_dead_list(terms: list[str], counts: dict[str, int]) -> str:
    dead = [term for term in terms if counts[term] == 0]
    return f"[{', '.join(dead)}]"


@dataclass(frozen=True)
class PreFilterSnapshot:
    prefilter_considered: int = 0
    prefilter_passed: int = 0
    prefilter_dropped: int = 0
    prefilter_blacklist_hits: int = 0


class PreFilterGate:
    def __init__(
        self,
        *,
        blacklist: list[str],
        dedup: _DedupStore,
        display: StatusDisplay,
        run_log: RunLog,
    ) -> None:
        self._blacklist = _precompute_blacklist(blacklist)
        self._bl_counts: dict[str, int] = {t: 0 for t in self._blacklist}
        self._dedup = dedup
        self._display = display
        self._run_log = run_log
        self._lock = threading.Lock()
        self._considered = 0
        self._passed = 0
        self._dropped = 0
        self._blacklist_hits = 0

    def admit(self, stub: _Stub) -> bool:
        verdict = _evaluate(stub, self._blacklist)
        title = stub.title or ""
        self._run_log.transcript(
            "pipeline_prefilter",
            {
                "url": stub.url,
                "title": title,
                "source": stub.source,
                "passes": verdict.passes,
                "reason": verdict.reason,
                "blacklist_matches": [
                    {"term": term} for term in verdict.blacklist_matches
                ],
                "title_len": len(title),
            },
        )
        for term in verdict.blacklist_matches:
            self._bl_counts[term] += 1
        with self._lock:
            self._considered += 1
            if verdict.passes:
                self._passed += 1
            else:
                self._dropped += 1
                if verdict.blacklist_matches:
                    self._blacklist_hits += 1
        if not verdict.passes:
            self._dedup.mark_out_of_domain(stub)
        return verdict.passes

    def snapshot(self) -> PreFilterSnapshot:
        with self._lock:
            return PreFilterSnapshot(
                prefilter_considered=self._considered,
                prefilter_passed=self._passed,
                prefilter_dropped=self._dropped,
                prefilter_blacklist_hits=self._blacklist_hits,
            )

    def emit_run_complete(self) -> None:
        self._run_log.event(
            "pipeline_prefilter",
            "run_complete",
            blacklist_keyword_hits=_format_keyword_hits(
                self._blacklist, self._bl_counts
            ),
            NEGATIVE_KEYWORDS_dead=_format_dead_list(self._blacklist, self._bl_counts),
        )
