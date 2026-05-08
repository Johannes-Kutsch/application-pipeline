from __future__ import annotations

import pytest

from application_pipeline.parsers.jobs_beim_staat_html import JobsBeimStaatParser


@pytest.mark.smoke
def test_discover_hamburg_returns_stubs_and_enrich_populates_description() -> None:
    with JobsBeimStaatParser(locations=["hamburg"], max_results=5) as p:
        stubs = list(p.discover(""))
    assert len(stubs) >= 1

    with JobsBeimStaatParser(locations=["hamburg"], max_results=5) as p:
        pos = p.enrich(stubs[0])
    assert pos.raw_description != ""
