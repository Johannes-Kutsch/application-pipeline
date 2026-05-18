from __future__ import annotations

import pytest

from application_pipeline.parsers.bundesagentur_api import BundesagenturParser
from application_pipeline.parsers.types import (
    City,
    ParserQuery,
    Position,
    PositionStub,
    Remote,
)


@pytest.mark.smoke
def test_discover_hamburg_returns_stubs_and_enrich_populates_description() -> None:
    query = ParserQuery(keyword="Python", location=City("Hamburg"), max_results=5)
    with BundesagenturParser() as p:
        stubs = [s for s in p.discover(query) if isinstance(s, PositionStub)]
    assert len(stubs) >= 1

    with BundesagenturParser() as p:
        pos = p.enrich(stubs[0])
    assert isinstance(pos, Position)
    assert pos.raw_description != ""
    assert pos.stub.source == "Bundesagentur"


@pytest.mark.smoke
def test_at_least_one_externe_url_exists_in_broad_search() -> None:
    query = ParserQuery(keyword="Ingenieur", location=Remote(), max_results=100)
    with BundesagenturParser() as p:
        stubs = [s for s in p.discover(query) if isinstance(s, PositionStub)]
    assert len(stubs) >= 1

    externe_url_found = False
    with BundesagenturParser() as p:
        for stub in stubs[:25]:
            result = p.enrich(stub)
            if hasattr(result, "outbound_url"):
                externe_url_found = True
                break
    assert externe_url_found, (
        "No externeURL-bearing item found in broad search — Arbeitsagentur may have renamed or removed the field"
    )
