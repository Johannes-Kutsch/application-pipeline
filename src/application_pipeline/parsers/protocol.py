from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from .types import ExternalRedirect, NotServedQuery, ParserQuery, Position, PositionStub


@runtime_checkable
class Parser(Protocol):
    body_selector: str | None

    def __enter__(self) -> Parser: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None: ...

    def discover(
        self, query: ParserQuery
    ) -> Iterable[PositionStub | NotServedQuery]: ...

    def enrich(self, stub: PositionStub) -> Position | ExternalRedirect: ...
