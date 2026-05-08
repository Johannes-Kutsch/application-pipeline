from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from .types import Position, PositionStub


@runtime_checkable
class Parser(Protocol):
    def __enter__(self) -> Parser: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None: ...

    def discover(self, query: str) -> Iterator[PositionStub]: ...

    def enrich(self, stub: PositionStub) -> Position: ...
