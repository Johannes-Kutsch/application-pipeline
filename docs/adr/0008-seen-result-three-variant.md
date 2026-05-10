# `is_seen` returns a 3-variant `SeenResult` (supersedes ADR-0004's rejected alternative)

`DeduplicationStore.is_seen(stub)` returns a `SeenResult` enum with three variants — `url_hit`, `tuple_hit`, `miss` — instead of a bare `bool`. The alias-write side effect described in ADR-0004 is preserved unchanged; only the return type widens.

## Why

ADR-0004 considered and rejected a 3-valued return on the grounds that *"the orchestrator has nothing to decide; it would always record the alias."* That premise no longer holds once the **Pipeline Orchestrator** implements the **discover-short-circuit** behaviour mandated by PRD #19 round-2 #13 and PRD #23 round-2 #3:

> After N consecutive stubs whose **URL** was already seen, the orchestrator stops consuming the generator and signals the parser thread to move to its next `(keyword, location)` query. **Tuple-tier dedup hits do NOT count** — syndicated copies do not appear newest-first.

The orchestrator now genuinely needs to distinguish "this URL was already in the store" from "we matched via the cross-URL tuple." A bare `bool` cannot carry that bit, and pre-checking `dedup.url_known(stub.url)` before calling `is_seen` would split the dedup API into two methods that must be called in sequence (with the obvious risk of forgetting the second call).

- **Closes the short-circuit correctness gap.** Counting tuple hits toward the threshold would let a syndication burst (e.g., a Bundesagentur listing also indexed by jobs-beim-staat) prematurely terminate `discover()` while genuinely-new newer listings sit untraversed in the tail. With `SeenResult`, the counter is exact.
- **Keeps the API single-call.** One method (`is_seen`) returns everything the caller needs to decide; the alias-write side effect (ADR-0004) still happens transparently. No `record_alias` second method, no two-step protocol.
- **Variant is type-checked, not stringly-typed.** A `SeenResult` enum (or `Literal["url_hit", "tuple_hit", "miss"]`) lets `mypy` catch missed branches in the orchestrator's match statement.

## Considered alternatives

- **Keep `is_seen -> bool`; add `dedup.url_known(url) -> bool`.** Rejected: forces two calls per stub at the orchestrator's hot path and creates an obvious "did I forget the second check" footgun. Splitting the cheap-vs-expensive dedup tier across two methods also leaks dedup-internal structure into the orchestrator.
- **Drop the short-circuit feature in v1.** Rejected: per-source `max_results` already bounds first-run cost, but on subsequent runs against a high-recall source (Bundesagentur with `max_results=1000`) the cron tick walks 1000 stubs paying one cheap URL lookup each — every run, forever — instead of stopping after ~50 consecutive seens. The wall-clock saving is small but real and the PRD already committed to it; dropping it now would re-open settled scope.
- **Count tuple hits toward the short-circuit (the simplest possible code).** Rejected: violates the strict-newest-first invariant the parsers committed to. A correctness regression for code-size reasons is the wrong trade.
- **`SeenResult` carries the matched record's `status` too.** Rejected for v1: the orchestrator never reads the existing `status` — it only decides whether to enrich. Adding it speculatively would broaden the surface for no caller. Re-add when a caller actually needs it.

## Consequences

- The `Parser`-facing protocol is unchanged. Parsers do not call `is_seen`; the orchestrator does.
- ADR-0004's alias-write side effect remains. The `is_seen` docstring still must call out the side effect for readers who would otherwise expect a pure read.
- `mark_seen` is unchanged — it still accepts `(key, status)` and still no-ops on URLs already present (which is the correct behaviour for a stub that was tuple-aliased during `is_seen`: the alias entry already carries the original's status).
- The orchestrator's discover loop matches on `SeenResult`:
  - `miss` → push `ENRICH`, reset short-circuit counter.
  - `url_hit` → push `SKIP`, increment counter; on threshold push `SKIP_AND_END_QUERY` (per ADR-0009).
  - `tuple_hit` → push `SKIP`, do **not** touch the counter.
- Tests against `DeduplicationStore` must re-assert on `SeenResult` variants rather than truthy bools.
- ADR-0004 is partially superseded: its "Considered alternatives" list rejected this exact return shape on grounds that no longer apply. ADR-0004's *core* decision (the alias write itself) stands.
