# Walk full per-query result tail; no dedup-driven discover early-stop

The orchestrator consumes every stub a **Parser** yields per `(keyword, location)` query up to the source's `max_results` cap. Stubs that hit the **Deduplication Store** are skipped (no `enrich()` paid), but the orchestrator does NOT truncate the query based on a run of consecutive already-seen URLs. `SKIP_AND_END_QUERY` (from ADR-0007) is retained but is now only emitted on `run_state.is_aborted`.

## Why

- **`discover()` is cheap; the saving doesn't justify the failure mode.** A truncation heuristic ("stop after N consecutive seen URLs") would silently kill information when a burst of prefilter-rejected listings (`out_of_domain`) near the top of the tail looks identical to a burst of previously-kept listings. The per-source `max_results` cap already bounds first-run cost; subsequent runs pay one URL-tier dict lookup per stub — negligible vs per-host pacing.
- **A correct variant requires distinguishing "previously kept" from "previously dropped".** That widens `SeenResult` and adds per-status branching in the hot path — complexity worth paying only if `discover()` were a real bottleneck. It isn't.
- **Per-host pacing dominates wall-clock.**
- **One fewer parser invariant.** `discover()` no longer has to be strictly newest-first as a *binding correctness* rule — only as a soft preference so `max_results` intersects genuinely-new listings on first run.

## Consequences

- Discover loop pushes `SKIP` for every `url_hit`/`tuple_hit` without per-parser state. No threshold knob.
- `SKIP_AND_END_QUERY` is retained; fired only on `run_state.is_aborted` so an aborting run drains worklists quickly.
- `SeenResult` keeps its variants (extended to 4 by ADR-0022) to drive the per-variant counters.
- Pagination newest-first is a soft preference — parsers that cannot sort newest-first pay an accuracy cost on first run / post-wipe only.
