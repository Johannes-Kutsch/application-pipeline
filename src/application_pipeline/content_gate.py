from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal, Protocol

from application_pipeline.parser_log import RunLog
from application_pipeline.status_display import StatusDisplay


class _Stub(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def title(self) -> str | None: ...

    @property
    def source(self) -> str: ...


_Reason = Literal["passed", "empty_body", "too_short"]

_MIN_BODY_LEN = 100


@dataclass(frozen=True)
class ContentSnapshot:
    content_considered: int = 0
    content_passed: int = 0
    content_dropped_empty_body: int = 0
    content_dropped_too_short: int = 0


def _evaluate(stripped_body: str) -> tuple[bool, _Reason]:
    if not stripped_body.strip():
        return False, "empty_body"
    if len(stripped_body.strip()) < _MIN_BODY_LEN:
        return False, "too_short"
    return True, "passed"


class ContentGate:
    def __init__(self, *, display: StatusDisplay, run_log: RunLog) -> None:
        self._display = display
        self._run_log = run_log
        self._lock = threading.Lock()
        self._content_considered = 0
        self._content_passed = 0
        self._content_dropped_empty_body = 0
        self._content_dropped_too_short = 0

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
        with self._lock:
            self._content_considered += 1
            if passes:
                self._content_passed += 1
            elif reason == "too_short":
                self._content_dropped_too_short += 1
            else:
                self._content_dropped_empty_body += 1
        return passes

    def snapshot(self) -> ContentSnapshot:
        with self._lock:
            return ContentSnapshot(
                content_considered=self._content_considered,
                content_passed=self._content_passed,
                content_dropped_empty_body=self._content_dropped_empty_body,
                content_dropped_too_short=self._content_dropped_too_short,
            )

    def emit_run_complete(self) -> None:
        with self._lock:
            considered = self._content_considered
            passed = self._content_passed
            dropped_empty = self._content_dropped_empty_body
            dropped_short = self._content_dropped_too_short
        self._run_log.event(
            "pipeline_content",
            "run_complete",
            content_considered=considered,
            content_passed=passed,
            content_dropped_empty_body=dropped_empty,
            content_dropped_too_short=dropped_short,
        )
