# Walk full per-query result tail; no dedup-driven discover early-stop

The **Pipeline Orchestrator** consumes every stub a **Parser** yields for the current `(keyword, location)` query up to the source's `max_results` cap. Stubs that hit the **Deduplication Store** are skipped (no `enrich()` paid), but the orchestrator does **not** truncate the query based on a run of consecutive already-seen URLs. `SKIP_AND_END_QUERY` remains the parser-side signal for abandoning the current generator (per ADR-0009) but is now only emitted on `run_state.is_aborted` — i.e. when a worker thread sets the fatal abort flag (per ADR-0017).

## Why

- **`discover()` is cheap; the saving doesn't justify the failure mode.** A truncation heuristic ("stop after N consecutive previously-seen URLs") saves a few list-page fetches per query. Against that: every URL the **Domain Pre-Filter** drops is `mark_off_domain`-ed and is indistinguishable on the next run from a URL that was previously enriched and **kept**. A burst of prefilter-rejected listings near the top of the newest-first tail therefore triggers the truncation before genuinely-new relevant listings further down are reached — a silent loss-of-information path with no diagnostic surface. The per-source `max_results` cap (default 1000) already bounds first-run cost; subsequent runs pay one URL-tier dict lookup per stub, which is negligible relative to per-host pacing inside the parser.

- **A correct variant of the heuristic would require distinguishing "previously kept" from "previously dropped" at the call site.** That means widening `SeenResult` (or `is_seen`'s contract) to carry the matched record's `status`, plus per-status branching in the orchestrator's hot path, plus a documented rule for each `status` value. Cheap to implement; expensive to keep coherent across future statuses and worth-the-complexity only if `discover()` were a real bottleneck. It isn't.

- **Per-host pacing dominates wall-clock.** A pessimistic estimate: a high-recall source returning 1000 stubs per query, with the Cartesian `KEYWORDS × LOCATIONS × SOURCES` expansion the **Pipeline Orchestrator** owns. Each cron tick walks that full set; **Deduplication** absorbs the overlap. The wall-clock is dominated by parser-internal request pacing, not by stub count consumed.

- **One fewer parser invariant.** `discover()` no longer has to be strictly newest-first as a *binding correctness* rule — only as a soft preference so the `max_results` cap intersects the genuinely-new listings on first run / post-`.seen.json`-reset. Sources whose default ordering is "approximately reverse-chronological" no longer carry a hidden silent-data-loss risk.

## Considered alternatives

- **Carve out non-kept statuses from the counter.** Only `kept` URL-hits would count toward the threshold; `off_domain`, `enrich_failed`, `external_redirect` would reset / not increment. Rejected: requires widening `SeenResult` (or routing `status` through the API), grows the dedup→orchestrator coupling, and the saved list-page fetches don't justify the new surface.

- **Stop marking prefilter-drops in the dedup store.** Removes the poisoning at the source. Rejected: the prefilter runs *after* `enrich()` (it inspects `title + raw_description`, only present on the enriched `Position`), so the mark earns its keep by skipping the detail-page fetch on the next encounter — actual HTTP cost, not just a counter quirk.

- **Collapse `SeenResult` back to `bool` once the counter is gone.** Rejected: the 3-variant return is the only operator-facing signal for tuple-tier effectiveness (`dedup_tuple_hits` on the **Run Divider**). Knowing whether the alias-write logic (ADR-0004) is doing real work justifies the slightly wider return type (see ADR-0008).

## Consequences

- The orchestrator's discover loop pushes `SKIP` for every `url_hit` and `tuple_hit` without per-parser state. No `consecutive_url_hits` field on the parser state, no `threshold` knob on the orchestrator or `Config`.
- `SKIP_AND_END_QUERY` is retained as the parser-side abandon-current-generator signal (per ADR-0009), now fired only when `run_state.is_aborted` is set, so an aborting run can drain its parser worklists quickly without processing every remaining stub.
- `SeenResult` remains a 3-variant `Literal["url_hit", "tuple_hit", "miss"]`. The two hit variants share a code path in the orchestrator; the variants survive to drive the Run Divider's `dedup_url_hits` / `dedup_tuple_hits` counters (see ADR-0008).
- **Pagination** newest-first is a soft preference rather than a binding invariant. Parsers that cannot sort newest-first pay an accuracy cost on first run / post-`.seen.json`-reset only — they no longer risk silent truncation on later runs.
