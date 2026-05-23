from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import assert_never

import pytest

from application_pipeline.parsers import (
    Parser,
    ParserError,
    ParserQuery,
    Position,
    PositionStub,
)
from application_pipeline.config.types import ConfigError
from application_pipeline.parsers.location import (
    NotServed,
    RemoteWire,
    Resolved,
    resolve,
    validate_coverage,
)
from application_pipeline.parsers.registry import get
from application_pipeline.parsers.types import City, Location, Remote


class _ConcreteParser:
    body_selector: str | None = None

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


def test_parser_query_valid_city():
    q = ParserQuery(keyword="python", location=City("Hamburg"), max_results=10)
    assert q.keyword == "python"
    assert q.location == City("Hamburg")
    assert q.max_results == 10


def test_parser_query_valid_remote():
    q = ParserQuery(keyword="java", location=Remote(), max_results=50)
    assert q.location == Remote()


def test_parser_query_rejects_empty_keyword():
    with pytest.raises(ValueError, match="keyword"):
        ParserQuery(keyword="", location=Remote(), max_results=10)


def test_parser_query_rejects_zero_max_results():
    with pytest.raises(ValueError, match="max_results"):
        ParserQuery(keyword="python", location=Remote(), max_results=0)


def test_parser_query_rejects_negative_max_results():
    with pytest.raises(ValueError, match="max_results"):
        ParserQuery(keyword="python", location=Remote(), max_results=-1)


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
    q = ParserQuery(keyword="python", location=Remote(), max_results=10)
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


def test_registry_get_result_is_instantiable(tmp_path: Path) -> None:
    from application_pipeline.parser_log import RunLog

    cls = get("bundesagentur_api")
    assert cls is not None
    assert isinstance(cls(run_log=RunLog(tmp_path)), Parser)  # type: ignore[call-arg]


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


# --- resolve() ---


class _FakeParser:
    def __init__(
        self,
        *,
        served_cities: set[str],
        serves_remote: bool,
        wire_suffix: str = "_wire",
    ) -> None:
        self._served_cities = served_cities
        self.serves_remote = serves_remote
        self._wire_suffix = wire_suffix

    def serves(self, name: str) -> bool:
        return name in self._served_cities

    def to_wire(self, name: str) -> str:
        return name + self._wire_suffix

    def remote_wire(self) -> str:
        return "remote_payload"


def test_resolve_city_served_returns_resolved() -> None:
    parser = _FakeParser(served_cities={"hamburg"}, serves_remote=False)
    result = resolve(City(name="Hamburg"), parser)
    assert result == Resolved(wire="hamburg_wire")


def test_resolve_city_not_served_returns_not_served() -> None:
    parser = _FakeParser(served_cities=set(), serves_remote=False)
    result = resolve(City(name="Hamburg"), parser)
    assert result == NotServed()


def test_resolve_remote_served_returns_remote_wire() -> None:
    parser = _FakeParser(served_cities=set(), serves_remote=True)
    result = resolve(Remote(), parser)
    assert result == RemoteWire(payload="remote_payload")


def test_resolve_remote_not_served_returns_not_served() -> None:
    parser = _FakeParser(served_cities=set(), serves_remote=False)
    result = resolve(Remote(), parser)
    assert result == NotServed()


def test_resolve_normalizes_city_name_before_lookup() -> None:
    # serves() receives a casefolded name; "München" → "münchen"
    parser = _FakeParser(served_cities={"münchen"}, serves_remote=False)
    result = resolve(City(name="München"), parser)
    assert result == Resolved(wire="münchen_wire")


def test_resolve_whitespace_only_city_returns_not_served() -> None:
    parser = _FakeParser(served_cities={"hamburg"}, serves_remote=False)
    result = resolve(City(name="   "), parser)
    assert result == NotServed()


# --- validate_coverage() ---


def test_validate_coverage_valid_config_passes_silently() -> None:
    parser = _FakeParser(served_cities={"hamburg", "berlin"}, serves_remote=True)
    validate_coverage([parser], locations=["Hamburg", "Berlin"], include_remote=True)


def test_validate_coverage_unservable_city_raises_with_offending_entry() -> None:
    parser = _FakeParser(served_cities={"hamburg"}, serves_remote=False)
    with pytest.raises(ConfigError, match="Atlantis"):
        validate_coverage([parser], locations=["Atlantis"], include_remote=False)


def test_validate_coverage_unservable_city_message_includes_close_match_hint() -> None:
    parser = _FakeParser(
        served_cities={"hamburg", "berlin", "munich"}, serves_remote=False
    )
    with pytest.raises(ConfigError, match="hamburg"):
        validate_coverage([parser], locations=["hamburh"], include_remote=False)


def test_validate_coverage_include_remote_without_remote_source_raises() -> None:
    class _NamedFake(_FakeParser):
        __name__ = "stellen_hamburg_api"

    parser = _NamedFake(served_cities={"hamburg"}, serves_remote=False)
    with pytest.raises(ConfigError) as excinfo:
        validate_coverage([parser], locations=["Hamburg"], include_remote=True)
    msg = str(excinfo.value)
    assert "remote" in msg.lower()
    assert "stellen_hamburg_api" in msg


def test_validate_coverage_lambda_true_parser_does_not_crash() -> None:
    class _NationwideFake:
        serves_remote = False

        def serves(self, name: str) -> bool:
            return True

        def to_wire(self, name: str) -> str:
            return name

        def remote_wire(self) -> str:
            return ""

    validate_coverage(
        [_NationwideFake()], locations=["Atlantis", "Hamburg"], include_remote=False
    )
