# Walk full per-query result tail; no dedup-driven discover early-stop

Orchestrator consumes every stub a **Parser** yields per `(keyword, location)` query. Stubs hitting the **Deduplication Store** are skipped (no `enrich()` paid), but the orchestrator does NOT truncate based on consecutive already-seen URLs. `SKIP_AND_END_QUERY` retained only for `run_state.is_aborted`. `max_results` is retired per ADR-0041.

## Why

- `discover()` is cheap; a truncation heuristic would silently kill information when prefilter-rejected listings near the tail look identical to previously-kept ones.
- A correct variant requires distinguishing "previously kept" from "previously dropped" — complexity not worth paying when `discover()` is not a bottleneck.
- Per-host pacing dominates wall-clock.

## Consequences

- Discover loop pushes `SKIP` for every `url_hit`/`tuple_hit` without per-parser state.
- Pagination newest-first is a soft preference, not a binding correctness rule.
