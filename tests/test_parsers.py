from __future__ import annotations

import sys
import types
from collections.abc import Iterator

import pytest

from application_pipeline.parsers import (
    Parser,
    ParserError,
    Position,
    PositionStub,
    UnknownParserError,
)
from application_pipeline.parsers.registry import get_parser_class


class _ConcreteParser:
    def __enter__(self) -> "_ConcreteParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: str) -> Iterator[PositionStub]:
        return iter([])

    def enrich(self, stub: PositionStub) -> Position:
        return Position(stub=stub, raw_description="")


@pytest.fixture
def parser() -> _ConcreteParser:
    return _ConcreteParser()


@pytest.fixture
def stub() -> PositionStub:
    return PositionStub(url="https://example.com/1", title="Dev", source="test")


def _register_parser_module(
    monkeypatch: pytest.MonkeyPatch,
    parser_type: str,
    *,
    parser_class: type | None = None,
) -> None:
    name = f"application_pipeline.parsers.{parser_type}"
    mod = types.ModuleType(name)
    if parser_class is not None:
        mod.parser_class = parser_class  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, name, mod)


# --- Error hierarchy ---


def test_parser_error_preserves_message():
    err = ParserError("network timeout")
    assert str(err) == "network timeout"


def test_unknown_parser_error_is_parser_error():
    with pytest.raises(ParserError):
        raise UnknownParserError("missing")


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

        def discover(self, query: str) -> Iterator[PositionStub]:
            return iter([])

    assert not isinstance(_Bad(), Parser)


def test_parser_protocol_works_as_context_manager(parser: _ConcreteParser):
    with parser as p:
        result = list(p.discover("python"))
    assert result == []


def test_parser_enrich_returns_position(parser: _ConcreteParser, stub: PositionStub):
    position = parser.enrich(stub)
    assert isinstance(position, Position)
    assert position.stub is stub


# --- Registry ---


def test_get_parser_class_raises_unknown_parser_error_for_missing_module():
    with pytest.raises(UnknownParserError, match="nonexistent_xyz"):
        get_parser_class("nonexistent_xyz")


def test_get_parser_class_raises_unknown_parser_error_when_module_lacks_parser_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_parser_module(monkeypatch, "faketype")
    with pytest.raises(UnknownParserError, match="faketype"):
        get_parser_class("faketype")


def test_get_parser_class_returns_parser_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_parser_module(monkeypatch, "goodtype", parser_class=_ConcreteParser)
    assert get_parser_class("goodtype") is _ConcreteParser


def test_get_parser_class_result_is_instantiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_parser_module(monkeypatch, "instantiable", parser_class=_ConcreteParser)
    cls = get_parser_class("instantiable")
    assert isinstance(cls(), Parser)
