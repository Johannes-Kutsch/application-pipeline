# Status display: uniform per-stage counters; judge demoted to terminal message

Every stage reports three uniform counters — **queued, dropped, forwarded**. Per-parser rows fold gate drops inline as named counters (e.g. `68 discovered · 3 freshness · 30 dedup · 9 enrich_failed · 26 forwarded`). Zero-drop counters hidden. Freshness drops from pre-enrich and post-enrich summed into one. Judge loses persistent row — outcome logged as terminal message.

Each parser gets two rows: parser row (`K discovered · [enrich_failed if nonzero] · N forwarded`) and gates row (non-zero drop counters only, hidden when all zero, pinned below parser row).

`llm_classify_relevance` row: queued/dropped/forwarded plus `malformed` counter (unparseable LLM response) and `classifying` counter (in-flight LLM calls — dequeued but not yet completed); `queued` shows current queue depth, not cumulative.

## Consequences

- Per-gate rows (`pipeline_dedup`, `pipeline_prefilter`, `pipeline_freshness`, `pipeline_content`) and `llm_judge_match` row retire.
- Parsers without `has_native_enrich` skip `enrich_failed` counter.
- `RunMetrics` tracks per-gate drop counts per parser.
