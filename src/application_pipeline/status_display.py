from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from application_pipeline import parser_log


class StatusDisplay(Protocol):
    def register(
        self, name: str, *, order: int, phase: str = "starting", body: str = ""
    ) -> None: ...

    def update_phase(self, name: str, *, phase: str) -> None: ...

    def update_body(self, name: str, *, body: str) -> None: ...

    def remove(self, name: str) -> None: ...

    def stop(self) -> None: ...


@dataclass
class _RowState:
    name: str
    order: int
    phase: str
    body: str


class PlainStatusDisplay:
    def __init__(self) -> None:
        self._phases: dict[str, str] = {}

    def register(
        self, name: str, *, order: int, phase: str = "starting", body: str = ""
    ) -> None:
        self._phases[name] = phase
        parser_log.record(name, "registered", order=order, phase=phase)
        print(f"{name}: registered order={order} phase={phase}")

    def update_phase(self, name: str, *, phase: str) -> None:
        if self._phases.get(name) == phase:
            return
        self._phases[name] = phase
        parser_log.record(name, "phase_changed", phase=phase)
        print(f"{name}: phase={phase}")

    def update_body(self, name: str, *, body: str) -> None:
        pass

    def remove(self, name: str) -> None:
        self._phases.pop(name, None)
        parser_log.record(name, "removed")
        print(f"{name}: removed")

    def stop(self) -> None:
        pass


class RichStatusDisplay:
    def __init__(self) -> None:
        from rich.live import Live

        self._lock = threading.Lock()
        self._rows: dict[str, _RowState] = {}
        self._live = Live(self, refresh_per_second=4)
        self._live.start()

    def __rich_console__(self, console: object, options: object) -> object:
        from rich.table import Table

        with self._lock:
            rows = sorted(self._rows.values(), key=lambda r: r.order)

        table = Table(show_header=True)
        table.add_column("Name")
        table.add_column("Phase")
        table.add_column("Body")
        for row in rows:
            table.add_row(row.name, row.phase, row.body)
        yield table

    def register(
        self, name: str, *, order: int, phase: str = "starting", body: str = ""
    ) -> None:
        with self._lock:
            self._rows[name] = _RowState(name=name, order=order, phase=phase, body=body)
        parser_log.record(name, "registered", order=order, phase=phase)

    def update_phase(self, name: str, *, phase: str) -> None:
        with self._lock:
            row = self._rows.get(name)
            if row is None or row.phase == phase:
                return
            row.phase = phase
        parser_log.record(name, "phase_changed", phase=phase)

    def update_body(self, name: str, *, body: str) -> None:
        with self._lock:
            row = self._rows.get(name)
            if row is not None:
                row.body = body

    def remove(self, name: str) -> None:
        with self._lock:
            self._rows.pop(name, None)
        parser_log.record(name, "removed")

    def stop(self) -> None:
        self._live.stop()
