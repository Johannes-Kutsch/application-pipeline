from __future__ import annotations

import ast
from pathlib import Path

import application_pipeline.parser_lifecycle as parser_lifecycle


_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "application_pipeline"


def _imported_names(module_path: Path, imported_module: str) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == imported_module:
            names.update(alias.name for alias in node.names)
    return names


def test_orchestrator_imports_only_parser_lifecycle_facade_surface() -> None:
    imported = _imported_names(
        _SRC / "orchestrator.py",
        "application_pipeline.parser_lifecycle",
    )

    assert imported == {
        "ParserLifecycleCollaborators",
        "ParserLifecycleExecution",
        "ParserLifecyclePlan",
        "run_parser_lifecycle",
    }


def test_parser_lifecycle_exports_only_plan_facade_surface() -> None:
    assert set(parser_lifecycle.__all__) == {
        "ParserLifecycleCollaborators",
        "ParserLifecycleExecution",
        "ParserLifecyclePlan",
        "run_parser_lifecycle",
    }


def test_orchestrator_keeps_parser_lifecycle_private_symbols_out_of_module() -> None:
    hidden_symbols = {
        "_NotServedQuery",
        "_ParserDone",
        "_ParserDead",
        "_ParserThread",
        "_ParserState",
        "_OutboundDispatcher",
        "_log_parser_stalls",
    }

    tree = ast.parse((_SRC / "orchestrator.py").read_text(encoding="utf-8"))
    defined_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }

    assert hidden_symbols.isdisjoint(defined_names)


def test_orchestrator_test_surface_keeps_only_one_parser_lifecycle_full_run_smoke() -> (
    None
):
    orchestrator_test_source = (_ROOT / "tests" / "test_orchestrator.py").read_text(
        encoding="utf-8"
    )

    assert (
        "def test_orchestrator_parser_lifecycle_full_run_smoke("
        in orchestrator_test_source
    )
    for retired_test_name in {
        "test_parser_summary_written_to_run_log",
        "test_not_served_queries_counted_in_parser_log_summary",
        "test_parser_dead_writes_one_failure_report_even_with_distinct_timestamps",
        "test_parser_dead_failure_report_contains_parser_id_exception_type_and_traceback",
    }:
        assert f"def {retired_test_name}(" not in orchestrator_test_source
