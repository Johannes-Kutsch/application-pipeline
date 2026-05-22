from __future__ import annotations

from pathlib import Path

import pytest

from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.stellen_hamburg_api import StellenHamburgParser
from application_pipeline.parsers.types import City, ParserQuery, Position, PositionStub


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


@pytest.mark.smoke
def test_discover_hamburg_returns_stubs_and_enrich_populates_description(
    run_log: RunLog,
) -> None:
    query = ParserQuery(keyword="*", location=City("hamburg"), max_results=5)
    with StellenHamburgParser(run_log=run_log) as p:
        stubs = [s for s in p.discover(query) if isinstance(s, PositionStub)]
    assert len(stubs) >= 1

    with StellenHamburgParser(run_log=run_log) as p:
        pos = p.enrich(stubs[0])
    assert isinstance(pos, Position)
    assert pos.raw_description != ""
