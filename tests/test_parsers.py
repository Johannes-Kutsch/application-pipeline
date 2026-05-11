from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import FrozenInstanceError
from typing import assert_never

import pytest

from application_pipeline.parsers import (
    Parser,
    ParserError,
    ParserQuery,
    Position,
    PositionStub,
)
from application_pipeline.parsers.registry import get
from application_pipeline.parsers.types import City, Location, Remote


class _ConcreteParser:
    def __enter__(self) -> "_ConcreteParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: ParserQuery) -> Iterable[PositionStub]:
        return iter([])

    def enrich(self, stub: PositionStub) -> Position:
        return Position(stub=stub, raw_description="")


@pytest.fixture
def parser() -> _ConcreteParser:
    return _ConcreteParser()


@pytest.fixture
def stub() -> PositionStub:
    return PositionStub(url="https://example.com/1", title="Dev", source="test")


# --- ParserQuery ---


def test_parser_query_valid():
    q = ParserQuery(keyword="python", location="Hamburg", max_results=10)
    assert q.keyword == "python"
    assert q.location == "Hamburg"
    assert q.max_results == 10


def test_parser_query_location_none_is_valid():
    q = ParserQuery(keyword="java", location=None, max_results=50)
    assert q.location is None


def test_parser_query_rejects_empty_keyword():
    with pytest.raises(ValueError, match="keyword"):
        ParserQuery(keyword="", location=None, max_results=10)


def test_parser_query_rejects_zero_max_results():
    with pytest.raises(ValueError, match="max_results"):
        ParserQuery(keyword="python", location=None, max_results=0)


def test_parser_query_rejects_negative_max_results():
    with pytest.raises(ValueError, match="max_results"):
        ParserQuery(keyword="python", location=None, max_results=-1)


# --- Error hierarchy ---


def test_parser_error_preserves_message():
    err = ParserError("network timeout")
    assert str(err) == "network timeout"


# --- Parser Protocol ---


def test_conforming_class_satisfies_parser_protocol(parser: _ConcreteParser):
    assert isinstance(parser, Parser)


def test_class_missing_discover_does_not_satisfy_parser_protocol():
    class _Bad:
        def __enter__(self) -> "_Bad":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def enrich(self, stub: PositionStub) -> Position:
            return Position(stub=stub, raw_description="")

    assert not isinstance(_Bad(), Parser)


def test_class_missing_enrich_does_not_satisfy_parser_protocol():
    class _Bad:
        def __enter__(self) -> "_Bad":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def discover(self, query: ParserQuery) -> Iterable[PositionStub]:
            return iter([])

    assert not isinstance(_Bad(), Parser)


def test_parser_protocol_works_as_context_manager(parser: _ConcreteParser):
    q = ParserQuery(keyword="python", location=None, max_results=10)
    with parser as p:
        result = list(p.discover(q))
    assert result == []


def test_parser_enrich_returns_position(parser: _ConcreteParser, stub: PositionStub):
    position = parser.enrich(stub)
    assert isinstance(position, Position)
    assert position.stub is stub


# --- Registry ---


def test_registry_get_returns_bundesagentur_api():
    from application_pipeline.parsers.bundesagentur_api import BundesagenturParser

    assert get("bundesagentur_api") is BundesagenturParser


def test_registry_get_returns_stellen_hamburg_api():
    from application_pipeline.parsers.stellen_hamburg_api import StellenHamburgParser

    assert get("stellen_hamburg_api") is StellenHamburgParser


def test_registry_get_returns_jobs_beim_staat_html():
    from application_pipeline.parsers.jobs_beim_staat_html import JobsBeimStaatParser

    assert get("jobs_beim_staat_html") is JobsBeimStaatParser


def test_registry_get_returns_none_for_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        result = get("nonexistent_xyz")
    assert result is None


def test_registry_get_logs_warning_for_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        get("nonexistent_xyz")
    assert "unknown_parser_type" in caplog.text
    assert "nonexistent_xyz" in caplog.text


def test_registry_get_result_is_instantiable() -> None:
    cls = get("bundesagentur_api")
    assert cls is not None
    assert isinstance(cls(), Parser)


# --- Location types ---


def test_city_is_frozen() -> None:
    city = City(name="Hamburg")
    with pytest.raises(FrozenInstanceError):
        city.name = "Berlin"  # type: ignore[misc]


def test_remote_is_frozen() -> None:
    remote = Remote()
    with pytest.raises(FrozenInstanceError):
        remote.x = 1  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        (City(name="Hamburg"), "city:Hamburg"),
        (Remote(), "remote"),
    ],
)
def test_location_match_is_exhaustive(loc: Location, expected: str) -> None:
    match loc:
        case City(name=n):
            result = f"city:{n}"
        case Remote():
            result = "remote"
        case _ as unreachable:
            assert_never(unreachable)
    assert result == expected
