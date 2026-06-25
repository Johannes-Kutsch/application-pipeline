"""Tests for body_fetch.fetch_and_strip with ParserHttp integration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from application_pipeline.http import HttpStubNotRetryableError
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers.body_fetch import OversizedBodyError, fetch_and_strip
from application_pipeline.parsers.types import EnrichFailedError
from tests.parsers.http_helpers import (
    ScriptedParserHttpOutcome,
    make_scripted_parser_http,
)

if TYPE_CHECKING:
    from application_pipeline.parsers.http import ParserHttp


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path)


def _make_http(run_log: RunLog, *outcomes: ScriptedParserHttpOutcome) -> ParserHttp:
    return make_scripted_parser_http(run_log, *outcomes, sleep=lambda _: None)[0]


# ---------------------------------------------------------------------------
# Behavior 1: fetch_and_strip accepts http: ParserHttp and delegates HTTP
# ---------------------------------------------------------------------------


def test_fetch_and_strip_returns_stripped_text(run_log: RunLog, tmp_path: Path) -> None:
    html = b"<html><body><p>Hello world</p></body></html>"
    http = _make_http(run_log, html)
    result = fetch_and_strip(
        "https://example.com/job/1",
        body_selector="p",
        source="test",
        failures_dir=tmp_path,
        http=http,
    )
    assert "Hello world" in result


# ---------------------------------------------------------------------------
# Behavior 2: Non-retryable HTTP failures surface as EnrichFailedError
# ---------------------------------------------------------------------------


def test_fetch_and_strip_raises_enrich_failed_error_on_not_retryable(
    run_log: RunLog, tmp_path: Path
) -> None:
    http = _make_http(
        run_log,
        HttpStubNotRetryableError("not found: https://example.com/job/404"),
    )
    with pytest.raises(EnrichFailedError):
        fetch_and_strip(
            "https://example.com/job/404",
            body_selector=None,
            source="test",
            failures_dir=tmp_path,
            http=http,
        )


# ---------------------------------------------------------------------------
# Behavior 3: Oversized bodies still stash and raise OversizedBodyError
# ---------------------------------------------------------------------------


def test_fetch_and_strip_raises_oversized_body_error_and_stashes(
    run_log: RunLog, tmp_path: Path
) -> None:
    large_text = "x" * (8_000 * 4 + 1)
    html = f"<p>{large_text}</p>".encode()
    http = _make_http(run_log, html)

    with pytest.raises(OversizedBodyError):
        fetch_and_strip(
            "https://example.com/job/big",
            body_selector="p",
            source="testsrc",
            failures_dir=tmp_path,
            http=http,
        )

    stashed = list((tmp_path / "oversized").glob("*.html"))
    assert stashed, "raw HTML was not stashed before raising OversizedBodyError"
