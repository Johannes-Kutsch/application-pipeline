# Retire `enrich_failed` status — all per-stub failures are transient

`enrich_failed` dedup status retired. `mark_enrich_failed` deleted. URLs whose enrich or classify call failed remain absent from `seen.json` — re-discovered and retried next run. Same pattern as oversized bodies and malformed LLM outputs (ADR-0032). Natural discover falloff bounds the retry tail.

## Why

- No permanent blacklist on transient conditions — a 404 today may be a CDN glitch. Cost of re-fetching a genuinely dead URL once per run is negligible.
- Consistency with oversized/malformed patterns. `EnrichFailedError` was the sole exception — a terminal mark for per-URL 4xx.
- `verdict is None` was already misaligned — called `mark_enrich_failed` despite ADR-0032 stating malformed outputs are retried. This was a bug.

## Consequences

- `DeduplicationStore` drops to four methods: `mark_out_of_domain`, `mark_matched`, `mark_selected_by_judge`, `mark_expired`.
- Existing `enrich_failed` records in `seen.json` become inert — short-circuit via URL-tier dedup.
- `RunSummary.enrich_failed` metric survives as diagnostic; only `seen.json` write removed.
- Amends ADR-0038 (no `mark_enrich_failed` on `EnrichFailedError`). Amends ADR-0014 (status enum).
