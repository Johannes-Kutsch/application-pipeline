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
