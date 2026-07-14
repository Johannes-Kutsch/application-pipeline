# Retire `enrich_failed` status — all per-stub failures transient

`enrich_failed` dedup status retired. Failed URLs remain absent from `seen.json` — retried next run. Same pattern as oversized bodies and malformed LLM outputs (ADR-0024). Natural discover falloff bounds the retry tail.

## Why

- No permanent blacklist on transient conditions. Consistency with oversized/malformed patterns.

## Consequences

- `DeduplicationStore` drops to four methods: `mark_out_of_domain`, `mark_matched`, `mark_selected_by_judge`, `mark_expired`.
- Existing `enrich_failed` records become inert — short-circuit via URL-tier dedup.
