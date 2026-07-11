from __future__ import annotations

from itertools import permutations

from application_pipeline.project_order_optimizer import optimize_project_order


def test_empty_list_yields_nothing() -> None:
    result = list(optimize_project_order([]))
    assert result == []


def test_single_item_yields_nothing() -> None:
    result = list(optimize_project_order(["A"]))
    assert result == []


def test_two_items_yields_one_permutation() -> None:
    result = list(optimize_project_order(["A", "B"]))
    assert result == [["B", "A"]]


def test_three_items_exhaustive() -> None:
    original = ["A", "B", "C"]
    result = list(optimize_project_order(original))
    all_perms = {tuple(p) for p in permutations(original)}
    all_perms.discard(tuple(original))
    assert {tuple(p) for p in result} == all_perms


def test_original_never_yielded() -> None:
    original = ["A", "B", "C", "D"]
    result = list(optimize_project_order(original))
    assert original not in result


def test_ascending_kendall_tau_order() -> None:
    original = ["A", "B", "C", "D"]
    result = list(optimize_project_order(original))

    distances = [_kendall_tau(original, p) for p in result]
    assert distances == sorted(distances)


def test_four_items_exhaustive() -> None:
    original = ["A", "B", "C", "D"]
    result = list(optimize_project_order(original))
    all_perms = {tuple(p) for p in permutations(original)}
    all_perms.discard(tuple(original))
    assert {tuple(p) for p in result} == all_perms


def _kendall_tau(original: list[str], permutation: list[str]) -> int:
    pos = {v: i for i, v in enumerate(permutation)}
    n = len(original)
    count = 0
    for i in range(n):
        for j in range(i + 1, n):
            if pos[original[i]] > pos[original[j]]:
                count += 1
    return count
