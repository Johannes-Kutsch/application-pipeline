# Walk full per-query result tail; no dedup-driven discover early-stop

Orchestrator consumes every stub a **Parser** yields per query. Dedup-hit stubs skipped (no `enrich()` paid), but no truncation based on consecutive already-seen URLs.

## Why

- `discover()` cheap; truncation heuristic would silently kill information. Per-host pacing dominates wall-clock.

## Consequences

- Pagination newest-first is a soft preference, not a correctness rule.
