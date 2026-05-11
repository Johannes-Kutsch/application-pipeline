from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, assert_never

from application_pipeline.parsers.types import City, Location, Remote
from application_pipeline.text import normalize


@dataclass(frozen=True)
class Resolved:
    wire: str


@dataclass(frozen=True)
class NotServed:
    pass


@dataclass(frozen=True)
class RemoteWire:
    payload: Any


class LocationCoverage(Protocol):
    serves_remote: bool

    def serves(self, name: str) -> bool: ...
    def to_wire(self, name: str) -> str: ...
    def remote_wire(self) -> Any: ...


def resolve(
    location: Location, parser_module: LocationCoverage
) -> Resolved | NotServed | RemoteWire:
    match location:
        case City(name=name):
            normalized = normalize(name)
            if normalized is None or not parser_module.serves(normalized):
                return NotServed()
            return Resolved(parser_module.to_wire(normalized))
        case Remote():
            if parser_module.serves_remote:
                return RemoteWire(parser_module.remote_wire())
            return NotServed()
        case _ as unreachable:
            assert_never(unreachable)
