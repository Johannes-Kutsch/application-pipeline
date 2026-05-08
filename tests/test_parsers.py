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


def _stub() -> PositionStub:
    return PositionStub(url="https://example.com/1", title="Dev", source="test")


class _ConcreteParser:
    def __enter__(self) -> "_ConcreteParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: str) -> Iterator[PositionStub]:
        return iter([])

    def enrich(self, stub: PositionStub) -> Position:
        return Position(stub=stub, raw_description="")


# --- Error hierarchy ---


def test_parser_error_is_exception():
    with pytest.raises(ParserError):
        raise ParserError("boom")


def test_parser_error_preserves_message():
    err = ParserError("network timeout")
    assert str(err) == "network timeout"


def test_unknown_parser_error_is_parser_error():
    err = UnknownParserError("no such parser")
    assert isinstance(err, ParserError)


def test_unknown_parser_error_can_be_raised_as_parser_error():
    with pytest.raises(ParserError):
        raise UnknownParserError("missing")


# --- Parser Protocol ---


def test_conforming_class_satisfies_parser_protocol():
    assert isinstance(_ConcreteParser(), Parser)


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


def test_parser_protocol_works_as_context_manager():
    with _ConcreteParser() as p:
        result = list(p.discover("python"))
    assert result == []


def test_parser_enrich_returns_position():
    p = _ConcreteParser()
    stub = _stub()
    position = p.enrich(stub)
    assert isinstance(position, Position)
    assert position.stub is stub


# --- Registry ---


def test_get_parser_class_raises_unknown_parser_error_for_missing_module():
    with pytest.raises(UnknownParserError, match="nonexistent_xyz"):
        get_parser_class("nonexistent_xyz")


def test_get_parser_class_raises_unknown_parser_error_when_module_lacks_parser_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = types.ModuleType("application_pipeline.parsers.faketype")
    monkeypatch.setitem(sys.modules, "application_pipeline.parsers.faketype", mod)
    with pytest.raises(UnknownParserError, match="faketype"):
        get_parser_class("faketype")


def test_get_parser_class_returns_parser_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = types.ModuleType("application_pipeline.parsers.goodtype")
    mod.parser_class = _ConcreteParser  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "application_pipeline.parsers.goodtype", mod)
    result = get_parser_class("goodtype")
    assert result is _ConcreteParser


def test_get_parser_class_result_is_instantiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = types.ModuleType("application_pipeline.parsers.instantiable")
    mod.parser_class = _ConcreteParser  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "application_pipeline.parsers.instantiable", mod)
    cls = get_parser_class("instantiable")
    instance = cls()
    assert isinstance(instance, Parser)
