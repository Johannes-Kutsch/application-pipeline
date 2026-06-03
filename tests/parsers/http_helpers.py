from __future__ import annotations

from collections.abc import Callable

from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.http import (
    ParserHttp,
    ParserHttpTestSeam,
    ScriptedParserHttpOutcome,
    ScriptedParserHttpTransport,
)


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
            seam=ParserHttpTestSeam(transport=transport, sleep=sleep),
            retries=retries,
        ),
        transport,
    )
