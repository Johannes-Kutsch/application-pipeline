# `is_seen` returns a 3-variant `SeenResult`

`DeduplicationStore.is_seen(stub)` returns `SeenResult` — `url_hit` / `tuple_hit` / `miss` — instead of a bare `bool`. Later widened to 4-variant with `judge_pending` for pool re-discovery. Alias-write side effect (ADR-0003) preserved.

## Why

- Tuple-tier effectiveness needs an operator-visible signal — `dedup_url_hits` / `dedup_tuple_hits` in the run-end events row.
- Single-call API — one method returns everything; alias-write happens transparently.
- Variant is type-checked (`Literal[...]`), not stringly-typed.

## Consequences

- Parsers do not call `is_seen`; orchestrator does.
- Tests assert on variants, not truthy bools.
