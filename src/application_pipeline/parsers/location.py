from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any, Protocol, assert_never

from application_pipeline.config.types import ConfigError
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


_HINT_SEEDS = (
    "berlin",
    "hamburg",
    "münchen",
    "munich",
    "köln",
    "cologne",
    "frankfurt",
    "stuttgart",
    "düsseldorf",
    "leipzig",
    "dortmund",
    "essen",
    "bremen",
    "dresden",
    "hannover",
    "nürnberg",
)


def _hint_candidates(
    parser_modules: list[LocationCoverage], locations: list[str]
) -> list[str]:
    probes: set[str] = set(_HINT_SEEDS)
    for loc in locations:
        n = normalize(loc)
        if n is not None:
            probes.add(n)
    candidates: set[str] = set()
    for probe in probes:
        if any(src.serves(probe) for src in parser_modules):
            candidates.add(probe)
    return sorted(candidates)


def validate_coverage(
    parser_modules: list[LocationCoverage],
    locations: list[str],
    include_remote: bool,
) -> None:
    candidates = _hint_candidates(parser_modules, locations)
    for loc in locations:
        normalized = normalize(loc)
        if normalized is None or not any(
            src.serves(normalized) for src in parser_modules
        ):
            hint = difflib.get_close_matches(normalized or loc, candidates, n=3)
            hint_msg = f" did you mean: {', '.join(hint)}?" if hint else ""
            raise ConfigError(
                f"location {loc!r} is not served by any configured source.{hint_msg}"
            )
    if include_remote and not any(src.serves_remote for src in parser_modules):
        names = ", ".join(_source_name(src) for src in parser_modules)
        raise ConfigError(
            f"include_remote=True but no configured source supports remote "
            f"(configured: {names})"
        )


def _source_name(src: LocationCoverage) -> str:
    return getattr(src, "__name__", None) or type(src).__name__


def resolve(
    location: Location, parser_module: LocationCoverage
) -> Resolved | NotServed | RemoteWire:
    match location:
        case City(name=name):
            normalized = normalize(name)
            if normalized is not None and parser_module.serves(normalized):
                return Resolved(parser_module.to_wire(normalized))
            return NotServed()
        case Remote():
            if parser_module.serves_remote:
                return RemoteWire(parser_module.remote_wire())
            return NotServed()
        case _ as unreachable:
            assert_never(unreachable)
