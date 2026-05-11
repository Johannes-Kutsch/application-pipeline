from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from application_pipeline import config as config_module
from application_pipeline import dedup as dedup_module
from application_pipeline import layout as layout_module
from application_pipeline.config import ConfigError
from application_pipeline.dedup import DedupStoreError, DeduplicationStore
from application_pipeline.llm import (
    ExtractorUnreachableError,
    LLMExtractor,
    OllamaExtractor,
)
from application_pipeline.parsers import Parser
from application_pipeline.parsers import registry as _default_registry
from application_pipeline.prefilter import DomainPreFilter
from application_pipeline.prompts import PromptError, load_prompts
from application_pipeline.results import ResultsFileError, ResultsFileManager

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSummary:
    total_discovered: int = 0
    total_seen: int = 0
    total_kept: int = 0
    duration_seconds: float = 0.0


def run(
    config_path: Path,
    *,
    extractor: LLMExtractor | None = None,
    prefilter: DomainPreFilter | None = None,
    parser_registry: Callable[[str], type[Parser] | None] | None = None,
    dedup_store: DeduplicationStore | None = None,
    results_manager: ResultsFileManager | None = None,
) -> RunSummary:
    _start = time.monotonic()

    # Step 1: Load config
    try:
        cfg = config_module.load(config_path)
    except ConfigError as exc:
        _log.error("startup failed — config: %s", exc)
        raise

    # Steps 2-4: Load prompts, build extractor, prewarm
    if extractor is None:
        try:
            prompts = load_prompts(cfg)
        except PromptError as exc:
            _log.error("startup failed — prompts: %s", exc)
            raise
        extractor = OllamaExtractor(cfg, prompts)

    try:
        extractor.prewarm()
    except ExtractorUnreachableError as exc:
        _log.error("startup failed — extractor unreachable: %s", exc)
        raise

    # Step 5: Domain Pre-Filter
    if prefilter is None:
        prefilter = DomainPreFilter(
            inclusion_keywords=cfg.inclusion_keywords,
            negative_keywords=cfg.negative_keywords,
            skills=cfg.skills,
        )

    # Step 6: Resolve parser classes; unknown types are skipped (registry logs WARNING)
    _resolve = parser_registry if parser_registry is not None else _default_registry.get
    resolved: list[type[Parser]] = []
    for source in cfg.sources:
        cls = _resolve(source.parser_type)
        if cls is not None:
            resolved.append(cls)

    # Step 7: Dedup store
    if dedup_store is None:
        try:
            dedup_store = dedup_module.load(cfg.seen_store_path)
        except DedupStoreError as exc:
            _log.error("startup failed — dedup store: %s", exc)
            raise

    # Step 8: Results manager + initialization
    if results_manager is None:
        file_header = ""
        if cfg.layout is not None:
            lyt = layout_module.load(cfg.layout)
            file_header = lyt.file_header
        results_manager = ResultsFileManager(Path("results/current.md"), file_header)
    try:
        results_manager.ensure_initialized()
    except ResultsFileError as exc:
        _log.error("startup failed — results file: %s", exc)
        raise

    # Step 9: Enter parsers via ExitStack, iterate (empty in this skeleton)
    with ExitStack() as stack:
        for cls in resolved:
            stack.enter_context(cls())
        # TODO: parser iteration — 4b/4c/4d

    return RunSummary(duration_seconds=time.monotonic() - _start)
