# `is_seen` returns a 3-variant `SeenResult`

`DeduplicationStore.is_seen(stub)` returns a `SeenResult` value — `url_hit`, `tuple_hit`, `miss` — instead of a bare `bool`. (ADR-0022 widens this to a 4-variant adding `in_domain` for pool re-discovery.) The alias-write side effect (ADR-0003) is preserved.

## Why

- **Tuple-tier effectiveness needs an operator-visible signal.** The only way to know if the alias-write (ADR-0003) is doing real work is to count tuple-tier hits separately. `dedup_url_hits`/`dedup_tuple_hits` in the run-end events row (per ADR-0018) is the diagnostic justifying the alias-write's complexity.
- **Single-call API.** One method returns everything the orchestrator + metrics layer need; alias-write happens transparently. No `record_alias` second method.
- **Variant is type-checked, not stringly-typed.** A `Literal[...]` return lets `mypy` catch missed branches.

## Consequences

- Parsers do not call `is_seen`; orchestrator does.
- Discover loop matches on `SeenResult`: `miss` → `ENRICH`; `url_hit`/`tuple_hit` → `SKIP`, bump per-variant counter.
- Tests assert on variants, not truthy bools.
- A flat `tuple_hits=0` history across runs is the signal that would justify reconsidering ADR-0003's alias-write.
