# Status display: uniform per-stage counters; judge demoted to terminal message

The status display had ad-hoc per-row formats — parsers showed `X/Y queries · K stubs · M/N enriched`, gates showed pass/drop aggregates, the judge had its own row. With ADR-0051 moving gates inline onto parser threads, per-gate rows no longer make sense (they'd duplicate the parser row). The judge row added little value — it runs once at the end.

Decision: every pipeline stage reports three uniform counters — **queued, dropped, forwarded** — representing items entering, items rejected, and items passed to the next consumer. Per-parser rows fold gate drops inline as named counters (e.g. `68 discovered · 3 freshness · 30 dedup · 9 enrich_failed · 26 forwarded`). Zero-drop counters are hidden. Freshness drops from pre-enrich and post-enrich arms are summed into one `freshness` counter. The LLM classify row uses the same model. The judge loses its persistent status row and prints a terminal log message instead.

## Consequences

- Per-gate status rows (`pipeline_dedup`, `pipeline_prefilter`, `pipeline_freshness`, `pipeline_content`) retire. Their counters fold into the parser row that owns them.
- Parser rows without `has_native_enrich` skip the `enrich_failed` counter entirely.
- `RunMetrics` tracks per-gate drop counts per parser instead of global gate aggregates.
- `llm_judge_match` status row removed; judge outcome logged as a one-shot message.
