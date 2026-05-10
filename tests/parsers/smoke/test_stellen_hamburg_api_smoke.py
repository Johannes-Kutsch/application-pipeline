from __future__ import annotations

import pytest

from application_pipeline.parsers.stellen_hamburg_api import StellenHamburgParser
from application_pipeline.parsers.types import ParserQuery


@pytest.mark.smoke
def test_discover_hamburg_returns_stubs_and_enrich_populates_description() -> None:
    query = ParserQuery(keyword="*", location="hamburg", max_results=5)
    with StellenHamburgParser() as p:
        stubs = list(p.discover(query))
    assert len(stubs) >= 1

    with StellenHamburgParser() as p:
        pos = p.enrich(stubs[0])
    assert pos.raw_description != ""
