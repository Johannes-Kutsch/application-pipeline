from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.http import ParserHttp


@dataclass(frozen=True)
class ScriptedParserHttpRequest:
    url: str
    timeout: float


@dataclass(frozen=True)
class ScriptedParserHttpResponse:
    status: int
    content: bytes = b""
    headers: Mapping[str, str] | None = None

    @classmethod
    def redirect(cls, *, status: int, location: str) -> ScriptedParserHttpResponse:
        return cls(status=status, headers={"location": location})


ScriptedParserHttpOutcome = bytes | Exception | ScriptedParserHttpResponse


class ScriptedParserHttpTransport:
    def __init__(self, outcomes: list[ScriptedParserHttpOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[ScriptedParserHttpRequest] = []
        self._closed = False

    def get(self, url: str, *, timeout: float) -> httpx.Response:
        if self._closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        self.requests.append(ScriptedParserHttpRequest(url=url, timeout=timeout))
        if not self._outcomes:
            raise AssertionError("ScriptedParserHttpTransport ran out of outcomes")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        request = httpx.Request("GET", url)
        if isinstance(outcome, bytes):
            return httpx.Response(200, content=outcome, request=request)
        return httpx.Response(
            outcome.status,
            content=outcome.content,
            headers=outcome.headers,
            request=request,
        )

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> ScriptedParserHttpTransport:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def make_scripted_parser_http(
    run_log: RunLog,
    *outcomes: ScriptedParserHttpOutcome,
    retries: int = 3,
    sleep: Callable[[float], None],
) -> tuple[ParserHttp, ScriptedParserHttpTransport]:
    transport = ScriptedParserHttpTransport(list(outcomes))
    return (
        ParserHttp.for_test(
            run_log=run_log,
            transport=transport,
            retries=retries,
            sleep=sleep,
        ),
        transport,
    )
