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
from application_pipeline.config import ConfigError, SourceEntry
from application_pipeline.dedup import DedupStoreError, DeduplicationStore
from application_pipeline.language import Language, resolve_language
from application_pipeline.layout.types import Layout
from application_pipeline.llm import (
    ExtractorUnreachableError,
    LLMExtractor,
    MatchTier,
    MatchVerdict,
    OllamaExtractor,
)
from application_pipeline.parsers import Parser, ParserQuery, Position
from application_pipeline.parsers import registry as _default_registry
from application_pipeline.prefilter import DomainPreFilter
from application_pipeline.prompts import PromptError, load_prompts
from application_pipeline.renderer import render
from application_pipeline.results import ResultsFileError, ResultsFileManager

_log = logging.getLogger(__name__)

_DEFAULT_LAYOUT = Layout(
    tier_emoji={"green": "🟢", "amber": "🟡", "red": "🔴"},
    tier_color={"green": "#2ea043", "amber": "#d29922", "red": "#da3633"},
    placeholder_groups={},
    file_header="# Results\n\n",
    card_template="## {number}. {title}  {emoji}\n\n",
    headline_template="## {number}. {title}  {emoji}\n\n",
)


@dataclass(frozen=True)
class RunSummary:
    discovered: int = 0
    skipped: int = 0
    prefilter_dropped: int = 0
    classifier_dropped: int = 0
    written: int = 0
    green: int = 0
    amber: int = 0
    red: int = 0
    duration_seconds: float = 0.0


def run(
    config_path: Path,
    *,
    extractor: LLMExtractor | None = None,
    prefilter: DomainPreFilter | None = None,
    parser_registry: Callable[[str], type[Parser] | None] | None = None,
    dedup_store: DeduplicationStore | None = None,
    results_manager: ResultsFileManager | None = None,
    layout: Layout | None = None,
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
    resolved: list[tuple[type[Parser], SourceEntry]] = []
    for source in cfg.sources:
        cls = _resolve(source.parser_type)
        if cls is not None:
            resolved.append((cls, source))

    # Step 7: Dedup store
    if dedup_store is None:
        try:
            dedup_store = dedup_module.load(cfg.seen_store_path)
        except DedupStoreError as exc:
            _log.error("startup failed — dedup store: %s", exc)
            raise

    # Step 8: Layout + Results manager + initialization
    if layout is None:
        if cfg.layout is not None:
            layout = layout_module.load(cfg.layout)
        else:
            layout = _DEFAULT_LAYOUT

    if results_manager is None:
        results_manager = ResultsFileManager(
            Path("results/current.md"), layout.file_header
        )
    try:
        results_manager.ensure_initialized()
    except ResultsFileError as exc:
        _log.error("startup failed — results file: %s", exc)
        raise

    # Step 9: Enter parsers via ExitStack, iterate sources × keywords × locations
    discovered = 0
    skipped = 0
    prefilter_dropped = 0
    survivors: list[tuple[Position, Language]] = []

    locations: list[str | None] = list(cfg.locations)
    if cfg.include_remote:
        locations.append(None)

    with ExitStack() as stack:
        parsers: list[tuple[Parser, SourceEntry]] = [
            (stack.enter_context(cls()), source) for cls, source in resolved
        ]
        for parser, source in parsers:
            for keyword in cfg.keywords:
                for location in locations:
                    query = ParserQuery(
                        keyword=keyword,
                        location=location,
                        max_results=source.max_results,
                    )
                    for stub in parser.discover(query):
                        discovered += 1
                        if dedup_store.is_seen(stub) == "miss":
                            position = parser.enrich(stub)
                            language = resolve_language(position)
                            verdict = prefilter.classify(position, language)
                            if verdict.passes:
                                survivors.append((position, language))
                            else:
                                dedup_store.mark_seen(stub, "off_domain")
                                prefilter_dropped += 1
                        else:
                            skipped += 1

    # Step 10: Classify batch — all classify_relevance calls before any judge_match call
    classifier_dropped = 0
    in_domain: list[tuple[Position, Language]] = []
    for position, language in survivors:
        rel_verdict = extractor.classify_relevance(
            language, position.title, position.raw_description
        )
        if rel_verdict.in_domain:
            in_domain.append((position, language))
        else:
            dedup_store.mark_seen(position.stub, "off_domain")
            classifier_dropped += 1

    # Step 11: Judge batch — all judge_match calls after classify batch
    judged: list[tuple[Position, MatchVerdict]] = []
    for position, language in in_domain:
        match_verdict = extractor.judge_match(language, position.raw_description)
        judged.append((position, match_verdict))

    # Step 12: Render → append+fsync → mark_seen("kept"), strictly in order
    written = 0
    green = 0
    amber = 0
    red = 0
    for position, match_verdict in judged:
        number = results_manager.next_position_number()
        rendered = render(position, match_verdict, number, layout)
        results_manager.append(rendered)
        dedup_store.mark_seen(position.stub, "kept")
        written += 1
        if match_verdict.tier == MatchTier.green:
            green += 1
        elif match_verdict.tier == MatchTier.amber:
            amber += 1
        else:
            red += 1

    _log.info(
        "run complete: discovered=%d skipped=%d prefilter_dropped=%d "
        "classifier_dropped=%d written=%d green=%d amber=%d red=%d",
        discovered,
        skipped,
        prefilter_dropped,
        classifier_dropped,
        written,
        green,
        amber,
        red,
    )

    return RunSummary(
        duration_seconds=time.monotonic() - _start,
        discovered=discovered,
        skipped=skipped,
        prefilter_dropped=prefilter_dropped,
        classifier_dropped=classifier_dropped,
        written=written,
        green=green,
        amber=amber,
        red=red,
    )
