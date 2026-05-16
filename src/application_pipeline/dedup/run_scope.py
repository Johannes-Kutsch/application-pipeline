"""RunScopedDedup — in-run URL tier wrapper around DeduplicationStore."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator, Literal

if TYPE_CHECKING:
    from .store import DeduplicationStore, SeenResult, _SeenKey

RunScopedSeenResult = Literal["url_hit", "tuple_hit", "run_hit", "miss"]


class RunScopedDedup:
    """Wraps a DeduplicationStore, adding an ephemeral in-run URL tier."""

    def __init__(self, store: DeduplicationStore) -> None:
        self._store = store
        self._in_run: set[str] = set()
        self._lock = threading.Lock()

    def is_seen(self, key: _SeenKey) -> RunScopedSeenResult:
        with self._lock:
            if key.url in self._in_run:
                return "run_hit"

        result: SeenResult = self._store.is_seen(key)

        if result == "miss":
            with self._lock:
                self._in_run.add(key.url)

        return result

    def mark_off_domain(self, key: _SeenKey) -> None:
        self._store.mark_off_domain(key)

    def mark_kept(self, key: _SeenKey) -> None:
        self._store.mark_kept(key)

    def mark_enrich_failed(self, key: _SeenKey) -> None:
        self._store.mark_enrich_failed(key)

    def mark_external_redirect(self, key: _SeenKey) -> None:
        self._store.mark_external_redirect(key)

    def _clear(self) -> None:
        with self._lock:
            self._in_run.clear()


@contextmanager
def _run_scope(store: DeduplicationStore) -> Generator[RunScopedDedup, None, None]:
    scope = RunScopedDedup(store)
    try:
        yield scope
    finally:
        scope._clear()
