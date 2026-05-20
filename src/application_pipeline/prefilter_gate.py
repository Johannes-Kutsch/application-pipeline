from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from application_pipeline.parser_log import RunLog
from application_pipeline.prefilter import (
    PreFilterVerdict,
    TermMatch,
    classify_position,
    precompute_blacklist,
)
from application_pipeline.run_metrics import RunMetrics


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


class _Position(Protocol):
    @property
    def stub(self) -> _Stub: ...

    @property
    def title(self) -> str: ...


class _DedupStore(Protocol):
    def mark_out_of_domain(self, key: _Stub) -> None: ...


@dataclass(frozen=True)
class _PreFilterVerdict:
    passes: bool
    reason: Literal["passed", "blacklist_drop"]
    blacklist_matches: tuple[TermMatch, ...]


def _evaluate(position: _Position, blacklist: list[str]) -> _PreFilterVerdict:
    verdict = classify_position(position, blacklist)
    reason: Literal["passed", "blacklist_drop"] = (
        "passed" if verdict.passes else "blacklist_drop"
    )
    return _PreFilterVerdict(
        passes=verdict.passes,
        reason=reason,
        blacklist_matches=verdict.blacklist_matches,
    )


def _format_keyword_hits(terms: list[str], counts: dict[str, int]) -> str:
    return " ".join(f"{term}={counts[term]}" for term in terms)


def _format_dead_list(terms: list[str], counts: dict[str, int]) -> str:
    dead = [term for term in terms if counts[term] == 0]
    return f"[{', '.join(dead)}]"


class PreFilterGate:
    def __init__(
        self,
        *,
        blacklist: list[str],
        dedup: _DedupStore,
        metrics: RunMetrics,
        run_log: RunLog,
    ) -> None:
        self._blacklist = precompute_blacklist(blacklist)
        self._bl_counts: dict[str, int] = {t: 0 for t in self._blacklist}
        self._dedup = dedup
        self._metrics = metrics
        self._run_log = run_log

    def admit(self, position: _Position) -> bool:
        verdict = _evaluate(position, self._blacklist)
        self._run_log.transcript(
            "pipeline_prefilter",
            {
                "url": position.stub.url,
                "title": position.title,
                "source": position.stub.source,
                "passes": verdict.passes,
                "reason": verdict.reason,
                "blacklist_matches": [
                    {"term": m.term} for m in verdict.blacklist_matches
                ],
                "title_len": len(position.title),
            },
        )
        for match in verdict.blacklist_matches:
            self._bl_counts[match.term] += 1
        pf_verdict = PreFilterVerdict(
            passes=verdict.passes, blacklist_matches=verdict.blacklist_matches
        )
        if verdict.passes:
            self._metrics.prefilter_passed(pf_verdict)
        else:
            self._metrics.prefilter_dropped(pf_verdict)
            self._dedup.mark_out_of_domain(position.stub)
        return verdict.passes

    def emit_run_complete(self) -> None:
        self._run_log.event(
            "pipeline_prefilter",
            "run_complete",
            blacklist_keyword_hits=_format_keyword_hits(
                self._blacklist, self._bl_counts
            ),
            NEGATIVE_KEYWORDS_dead=_format_dead_list(self._blacklist, self._bl_counts),
        )
