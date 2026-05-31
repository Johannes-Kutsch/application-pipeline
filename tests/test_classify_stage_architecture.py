from __future__ import annotations

import ast
from pathlib import Path

import application_pipeline.classify_stage as classify_stage


_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "application_pipeline"


def _imported_names(module_path: Path, imported_module: str) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == imported_module:
            names.update(alias.name for alias in node.names)
    return names


def _owner_name(symbol_name: str) -> str | None:
    symbol = getattr(classify_stage, symbol_name)
    if symbol_name == "CLASSIFY_SHUTDOWN":
        return symbol.__class__.__module__
    return getattr(symbol, "__module__", None)


def test_parser_intake_imports_only_classify_stage_handoff() -> None:
    imported = _imported_names(
        _SRC / "parser_intake.py",
        "application_pipeline.classify_stage",
    )

    assert imported == {"ClassifyStageHandoff"}


def test_orchestrator_imports_only_classify_stage_facade_surface() -> None:
    imported = _imported_names(
        _SRC / "orchestrator.py",
        "application_pipeline.classify_stage",
    )

    assert imported == {
        "BatchLLMEnricher",
        "ClassifyStage",
        "ClassifyStageHandoff",
    }


def test_classify_stage_handoff_exposes_submit_ready_as_only_public_operation() -> None:
    public_methods = {
        name
        for name, value in classify_stage.ClassifyStageHandoff.__dict__.items()
        if callable(value) and not name.startswith("_")
    }

    assert public_methods == {"submit_ready"}


def test_classify_stage_ownership_guard_covers_owned_seam_symbols() -> None:
    expected_owned_symbols = {
        "ClassifyReadySubmission",
        "ClassifyRequest",
        "ClassifyShutdown",
        "CLASSIFY_SHUTDOWN",
        "ClassifyAccumulator",
        "ClassifyWorker",
        "_QueueBackedClassifyStageHandoff",
        "ClassifyStageMetrics",
        "ClassifyPoolCollector",
    }

    classify_stage.assert_classify_stage_ownership()

    assert expected_owned_symbols <= set(classify_stage.__dict__)
    assert {name: _owner_name(name) for name in expected_owned_symbols} == {
        name: classify_stage.__name__ for name in expected_owned_symbols
    }
