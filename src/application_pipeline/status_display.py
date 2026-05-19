from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

from application_pipeline.parser_log import RunLog


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


class _StatusDisplay:
    def __init__(self, *, run_log: RunLog | None) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, _RowState] = {}
        self._run_log = run_log

    def _snapshot_rows(self) -> list[_RowState]:
        with self._lock:
            return sorted(self._rows.values(), key=lambda r: r.order)

    # Protected hooks — subclasses override to add per-implementation behaviour.
    def _on_registered(self, name: str, order: int, phase: str) -> None:
        pass

    def _on_phase_changed(self, name: str, phase: str) -> None:
        pass

    def _on_body_changed(self, name: str, body: str) -> None:
        pass

    def _on_removed(self, name: str) -> None:
        pass

    def register(
        self, name: str, *, order: int, phase: str = "starting", body: str = ""
    ) -> None:
        with self._lock:
            self._rows[name] = _RowState(name=name, order=order, phase=phase, body=body)
            if self._run_log is not None:
                self._run_log.lifecycle(name, "registered", order=order, phase=phase)
            self._on_registered(name, order, phase)

    def update_phase(self, name: str, *, phase: str) -> None:
        with self._lock:
            row = self._rows.get(name)
            if row is None or row.phase == phase:
                return
            row.phase = phase
            if self._run_log is not None:
                self._run_log.lifecycle(name, "phase_changed", phase=phase)
            self._on_phase_changed(name, phase)

    def update_body(self, name: str, *, body: str) -> None:
        with self._lock:
            row = self._rows.get(name)
            if row is None:
                return
            row.body = body
            self._on_body_changed(name, body)

    def remove(self, name: str) -> None:
        with self._lock:
            self._rows.pop(name, None)
            if self._run_log is not None:
                self._run_log.lifecycle(name, "removed")
            self._on_removed(name)

    def print(self, *, caller: str, message: str) -> None:
        pass

    def stop(self) -> None:
        pass


class PlainStatusDisplay(_StatusDisplay):
    def __init__(self, *, run_log: RunLog | None) -> None:
        super().__init__(run_log=run_log)

    def _on_registered(self, name: str, order: int, phase: str) -> None:
        print(f"{name}: registered order={order} phase={phase}")

    def _on_phase_changed(self, name: str, phase: str) -> None:
        print(f"{name}: phase={phase}")

    def _on_removed(self, name: str) -> None:
        print(f"{name}: removed")


class RichStatusDisplay(_StatusDisplay):
    def __init__(self, *, run_log: RunLog | None) -> None:
        from rich.live import Live

        super().__init__(run_log=run_log)
        self._live = Live(self, refresh_per_second=4)  # type: ignore[arg-type]
        self._live.start()
        self._log_handler: logging.Handler = _LiveLoggingHandler(self)
        logging.getLogger().addHandler(self._log_handler)

    def __rich_console__(self, console: object, options: object) -> object:
        from rich.table import Table

        rows = self._snapshot_rows()
        table = Table(show_header=True)
        table.add_column("Name")
        table.add_column("Phase")
        table.add_column("Body")
        for row in rows:
            table.add_row(row.name, row.phase, row.body)
        yield table

    def print(self, *, caller: str, message: str) -> None:
        self._live.console.print(message)

    def stop(self) -> None:
        logging.getLogger().removeHandler(self._log_handler)
        self._live.stop()
