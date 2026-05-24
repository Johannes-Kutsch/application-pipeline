from __future__ import annotations

from pathlib import Path

import pytest

from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.bundesagentur_api import BundesagenturParser
from application_pipeline.parsers.types import (
    City,
    ParserQuery,
    PositionStub,
)


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


@pytest.mark.smoke
def test_discover_hamburg_returns_stubs(run_log: RunLog) -> None:
    query = ParserQuery(keyword="Python", location=City("Hamburg"))
    with BundesagenturParser(run_log=run_log) as p:
        stubs = [s for s in p.discover(query) if isinstance(s, PositionStub)]
    assert len(stubs) >= 1
    assert stubs[0].source == "Bundesagentur"
