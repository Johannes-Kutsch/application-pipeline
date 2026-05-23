from __future__ import annotations

from pathlib import Path

import pytest

from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.jobs_beim_staat_html import JobsBeimStaatParser
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
    query = ParserQuery(keyword="*", location=City("hamburg"), max_results=5)
    with JobsBeimStaatParser(run_log=run_log) as p:
        stubs = [s for s in p.discover(query) if isinstance(s, PositionStub)]
    assert len(stubs) >= 1
