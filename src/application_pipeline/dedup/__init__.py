from .errors import DedupStoreError
from .store import (
    DeduplicationStore,
    RunScopedSeenKind,
    RunScopedSeenResult,
    SeenResult,
    SeenStatus,
    load,
)

RunScopedDedup = DeduplicationStore

__all__ = [
    "DedupStoreError",
    "DeduplicationStore",
    "RunScopedDedup",
    "RunScopedSeenKind",
    "RunScopedSeenResult",
    "SeenResult",
    "SeenStatus",
    "load",
]
