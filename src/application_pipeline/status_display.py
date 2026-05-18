from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

from application_pipeline import parser_log


class StatusDisplay(Protocol):
    def register(
        self, name: str, *, order: int, phase: str = "starting", body: str = ""
    ) -> None: ...

    def update_phase(self, name: str, *, phase: str) -> None: ...

    def update_body(self, name: str, *, body: str) -> None: ...

    def remove(self, name: str) -> None: ...

    def print(self, *, caller: str, message: str) -> None: ...

    def stop(self) -> None: ...


@dataclass
class _RowState:
    name: str
    order: int
    phase: str
    body: str


class _LiveLoggingHandler(logging.Handler):
    """Routes stdlib logging records to a StatusDisplay while Rich Live is active."""

    def __init__(self, display: StatusDisplay) -> None:
        super().__init__()
        self._display = display
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._display.print(caller=record.name, message=self.format(record))
        except Exception:
            self.handleError(record)


class _DisplayRenderer(Protocol):
    def on_registered(self, name: str, order: int, phase: str) -> None: ...
    def on_phase_changed(self, name: str, phase: str) -> None: ...
    def on_body_changed(self, name: str, body: str) -> None: ...
    def on_removed(self, name: str) -> None: ...
    def print(self, caller: str, message: str) -> None: ...
    def stop(self) -> None: ...


class _PlainRenderer:
    def on_registered(self, name: str, order: int, phase: str) -> None:
        print(f"{name}: registered order={order} phase={phase}")

    def on_phase_changed(self, name: str, phase: str) -> None:
        print(f"{name}: phase={phase}")

    def on_body_changed(self, name: str, body: str) -> None:
        pass

    def on_removed(self, name: str) -> None:
        print(f"{name}: removed")

    def print(self, caller: str, message: str) -> None:
        pass

    def stop(self) -> None:
        pass


class _RichRenderer:
    def __init__(
        self,
        get_rows: Callable[[], list[_RowState]],
        display: StatusDisplay,
    ) -> None:
        from rich.live import Live

        self._get_rows = get_rows
        self._live = Live(self, refresh_per_second=4)  # type: ignore[arg-type]
        self._live.start()
        self._log_handler: logging.Handler = _LiveLoggingHandler(display)
        logging.getLogger().addHandler(self._log_handler)

    def __rich_console__(self, console: object, options: object) -> object:
        from rich.table import Table

        rows = self._get_rows()
        table = Table(show_header=True)
        table.add_column("Name")
        table.add_column("Phase")
        table.add_column("Body")
        for row in rows:
            table.add_row(row.name, row.phase, row.body)
        yield table

    def on_registered(self, name: str, order: int, phase: str) -> None:
        pass

    def on_phase_changed(self, name: str, phase: str) -> None:
        pass

    def on_body_changed(self, name: str, body: str) -> None:
        pass

    def on_removed(self, name: str) -> None:
        pass

    def print(self, caller: str, message: str) -> None:
        self._live.console.print(message)

    def stop(self) -> None:
        logging.getLogger().removeHandler(self._log_handler)
        self._live.stop()


class _StatusDisplay:
    def __init__(self, renderer: _DisplayRenderer) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, _RowState] = {}
        self._renderer = renderer

    def register(
        self, name: str, *, order: int, phase: str = "starting", body: str = ""
    ) -> None:
        with self._lock:
            self._rows[name] = _RowState(name=name, order=order, phase=phase, body=body)
            parser_log.record_lifecycle(name, "registered", order=order, phase=phase)
            self._renderer.on_registered(name, order, phase)

    def update_phase(self, name: str, *, phase: str) -> None:
        with self._lock:
            row = self._rows.get(name)
            if row is None or row.phase == phase:
                return
            row.phase = phase
            parser_log.record_lifecycle(name, "phase_changed", phase=phase)
            self._renderer.on_phase_changed(name, phase)

    def update_body(self, name: str, *, body: str) -> None:
        with self._lock:
            row = self._rows.get(name)
            if row is None:
                return
            row.body = body
            self._renderer.on_body_changed(name, body)

    def remove(self, name: str) -> None:
        with self._lock:
            self._rows.pop(name, None)
            parser_log.record_lifecycle(name, "removed")
            self._renderer.on_removed(name)

    def print(self, *, caller: str, message: str) -> None:
        self._renderer.print(caller, message)

    def stop(self) -> None:
        self._renderer.stop()


class PlainStatusDisplay(_StatusDisplay):
    def __init__(self) -> None:
        super().__init__(_PlainRenderer())


class RichStatusDisplay(_StatusDisplay):
    def __init__(self) -> None:
        super().__init__(
            _PlainRenderer()
        )  # sets up _lock and _rows before renderer needs them
        lock = self._lock
        rows = self._rows

        def get_rows() -> list[_RowState]:
            with lock:
                return sorted(rows.values(), key=lambda r: r.order)

        self._renderer = _RichRenderer(get_rows=get_rows, display=self)
