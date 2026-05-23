# Retire `enrich_failed` status — all per-stub failures are transient

The `enrich_failed` dedup status is retired. `mark_enrich_failed` is deleted from `DeduplicationStore`. No per-stub failure writes a terminal status to `seen.json`. URLs whose enrich or classify call failed remain absent from `seen.json` and are re-discovered, re-enriched, and re-classified on the next run — same pattern already established for oversized bodies (ADR-0041) and malformed LLM outputs (ADR-0041).

The two former call sites — `EnrichFailedError` catch in the enrich thread, and `verdict is None` (malformed classifier output) — both become silent skips: log event, increment metric, return. The URL stays unrecorded. Natural discover falloff (listings age out of search results) bounds the retry tail; no TTL or retry-cap mechanism is needed.

## Why

- **No permanent blacklist on transient conditions.** A 404 today may be a temporary CDN glitch or a page republished tomorrow. Marking it terminal forecloses re-check forever. The cost of re-fetching a genuinely dead URL once per run until it falls off discover results is negligible compared to permanently missing a listing that comes back.
- **Consistency.** Oversized bodies and malformed LLM outputs already follow the "no mark, retry next run" pattern (ADR-0041). `EnrichFailedError` was the sole exception — a terminal mark for per-URL 4xx. Unifying removes a special case from the status enum and the orchestrator's error-handling matrix.
- **`verdict is None` was already misaligned.** The classifier returning `None` (malformed LLM output) called `mark_enrich_failed` — a terminal skip — despite ADR-0041 explicitly stating that malformed outputs are stashed and retried. This was a bug, not a deliberate decision.

## Considered alternatives

- **Transient with a TTL — write a `enrich_pending` status with a timestamp, skip re-enrich for N days, then retry.** Rejected: adds complexity (new status, timestamp field, TTL config) for marginal gain. Discover falloff naturally bounds the retry window.
- **Keep `enrich_failed` for 404 only, make other failures transient.** Rejected: 404 on a detail endpoint can be transient (CDN, temporary removal). The terminal/transient distinction per HTTP status is not worth the complexity.

## Consequences

- **`DeduplicationStore.mark_enrich_failed()` deleted.** Five narrow methods become four: `mark_out_of_domain`, `mark_matched`, `mark_selected_by_judge`, `mark_expired`.
- **`enrich_failed` removed from the dedup status enum.** Existing `enrich_failed` records in `seen.json` become inert — they still short-circuit via URL-tier dedup (`is_seen` returns `url_hit`), so previously-failed URLs are not re-processed. No migration needed.
- **`EnrichFailedError` catch in `_EnrichThread._process()`** no longer calls `mark_enrich_failed`. Logs the event and returns (stub skipped, URL unrecorded).
- **`verdict is None` branch in `_EnrichThread._process()`** no longer calls `mark_enrich_failed`. Same treatment: log and return.
- **`RunSummary.enrich_failed` metric survives** — it still counts how many stubs hit enrich failures per run. The metric is diagnostic; only the `seen.json` write is removed.
- **CONTEXT.md updates**: dedup status enum drops `enrich_failed`. Error semantics for `mark_*` section updates. `EnrichResult` entry updates (no more `mark_enrich_failed` on raise). `DeduplicationStore` surface drops to four methods.

## Supersedes / amends

- **Amends ADR-0047**: reverses the "`enrich()` raises `EnrichFailedError` → orchestrator calls `mark_enrich_failed(stub)` — terminal-skip" decision. `EnrichFailedError` is still raised by `enrich()` and caught by the orchestrator, but the orchestrator no longer writes to `seen.json`.
- **Amends ADR-0020**: `enrich_failed` removed from the status enum.
