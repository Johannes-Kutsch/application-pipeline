from __future__ import annotations

import pytest

from application_pipeline.parsers.jobs_beim_staat_html import JobsBeimStaatParser
from application_pipeline.parsers.types import (
    City,
    ExternalRedirect,
    ParserQuery,
    Position,
    PositionStub,
)


@pytest.mark.smoke
def test_discover_hamburg_returns_stubs_and_enrich_populates_description() -> None:
    query = ParserQuery(keyword="*", location=City("hamburg"), max_results=5)
    with JobsBeimStaatParser() as p:
        stubs = [s for s in p.discover(query) if isinstance(s, PositionStub)]
    assert len(stubs) >= 1

    with JobsBeimStaatParser() as p:
        for stub in stubs:
            result = p.enrich(stub)
            if isinstance(result, Position):
                assert result.raw_description != ""
                return
            assert isinstance(result, ExternalRedirect)
            assert result.outbound_url != ""
