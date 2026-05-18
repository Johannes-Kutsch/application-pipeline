from .errors import DedupStoreError
from .store import DeduplicationStore, RunScopedSeenResult, SeenResult, SeenStatus, load

RunScopedDedup = DeduplicationStore

__all__ = [
    "DedupStoreError",
    "DeduplicationStore",
    "RunScopedDedup",
    "RunScopedSeenResult",
    "SeenResult",
    "SeenStatus",
    "load",
]
