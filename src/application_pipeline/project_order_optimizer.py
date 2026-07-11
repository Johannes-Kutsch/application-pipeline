from __future__ import annotations

from itertools import permutations
from typing import Iterator


def _kendall_tau(original: list[str], permutation: tuple[str, ...]) -> int:
    pos = {v: i for i, v in enumerate(permutation)}
    n = len(original)
    count = 0
    for i in range(n):
        for j in range(i + 1, n):
            if pos[original[i]] > pos[original[j]]:
                count += 1
    return count


def optimize_project_order(items: list[str]) -> Iterator[list[str]]:
    """Yield all permutations of items in ascending Kendall tau distance, excluding the original."""
    if len(items) <= 1:
        return

    original_tuple = tuple(items)
    candidates = [
        (p, _kendall_tau(items, p)) for p in permutations(items) if p != original_tuple
    ]
    candidates.sort(key=lambda x: x[1])

    for perm, _ in candidates:
        yield list(perm)
