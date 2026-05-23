from __future__ import annotations

from typing import Literal, Protocol

from application_pipeline.parser_log import RunLog
from application_pipeline.run_metrics import RunMetrics


class _Stub(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def title(self) -> str | None: ...

    @property
    def source(self) -> str: ...


_Reason = Literal["passed", "empty_body"]


def _evaluate(stripped_body: str) -> tuple[bool, _Reason]:
    if not stripped_body.strip():
        return False, "empty_body"
    return True, "passed"


class ContentGate:
    def __init__(self, *, metrics: RunMetrics, run_log: RunLog) -> None:
        self._metrics = metrics
        self._run_log = run_log
        self._content_considered = 0
        self._content_passed = 0
        self._content_dropped_empty_body = 0

    def admit(self, stripped_body: str, stub: _Stub) -> bool:
        passes, reason = _evaluate(stripped_body)
        self._run_log.transcript(
            "pipeline_content",
            {
                "url": stub.url,
                "title": stub.title,
                "source": stub.source,
                "passes": passes,
                "reason": reason,
                "body_len": len(stripped_body),
            },
        )
        self._content_considered += 1
        if passes:
            self._content_passed += 1
            self._metrics.content_passed()
        else:
            self._content_dropped_empty_body += 1
            self._metrics.content_dropped()
        return passes

    def emit_run_complete(self) -> None:
        self._run_log.event(
            "pipeline_content",
            "run_complete",
            content_considered=self._content_considered,
            content_passed=self._content_passed,
            content_dropped_empty_body=self._content_dropped_empty_body,
        )
