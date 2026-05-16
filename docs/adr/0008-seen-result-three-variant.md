# `is_seen` returns a 3-variant `SeenResult`

`DeduplicationStore.is_seen(stub)` returns a `SeenResult` value with three variants — `url_hit`, `tuple_hit`, `miss` — instead of a bare `bool`. The alias-write side effect described in ADR-0004 is preserved unchanged; only the return type widens.

## Why

- **Tuple-tier effectiveness needs an operator-visible signal.** ADR-0004 introduced the tuple → URL alias write so that the second time a syndicated copy is encountered it hits the cheap URL tier. The only way to know whether that logic is doing meaningful work is to count tuple-tier hits separately. The **Run Divider**'s `dedup_url_hits` / `dedup_tuple_hits` split is the diagnostic that justifies the alias-write complexity; collapsing `SeenResult` to `bool` would hide that signal and leave the alias-write logic un-auditable.

- **Single-call API.** One method returns everything the orchestrator and the metrics layer need; the alias-write side effect (ADR-0004) still happens transparently. No `record_alias` second method, no two-step protocol that the caller can forget.

- **Variant is type-checked, not stringly-typed.** A `Literal["url_hit", "tuple_hit", "miss"]` return lets `mypy` catch missed branches in the orchestrator's match statement and in the metrics layer.

## Considered alternatives

- **Keep `is_seen -> bool`; expose tier counts via a separate `DeduplicationStore.metrics()` method.** Rejected: splits the natural unit (a single dedup decision) across two API calls; the metrics layer would have to interrogate the store on a separate clock from the decision itself. The 3-variant return keeps the per-call cost the metrics layer counts trivially in sync with the actual decision stream.

- **`SeenResult` carries the matched record's `status` too.** Rejected: per ADR-0020, the orchestrator branches identically on `url_hit` regardless of the matched record's `status` — there is no `kept`-vs-`off_domain` distinction at this seam. Adding the field would broaden the surface for no caller. Re-add when one genuinely needs it.

## Consequences

- The `Parser`-facing protocol is unchanged. Parsers do not call `is_seen`; the orchestrator does.
- ADR-0004's alias-write side effect remains. The `is_seen` docstring still must call out the side effect for readers who would otherwise expect a pure read.
- The orchestrator's discover loop matches on `SeenResult`:
  - `miss` → push `ENRICH`.
  - `url_hit` → push `SKIP`; bump `dedup_url_hits`.
  - `tuple_hit` → push `SKIP`; bump `dedup_tuple_hits`.
- Tests against `DeduplicationStore` assert on `SeenResult` variants rather than truthy bools.
- The Run Divider records `dedup_url_hits` and `dedup_tuple_hits` separately. A flat-`tuple_hits=0` history across runs is the signal that would justify reconsidering the alias-write logic (ADR-0004); a non-zero count is the proof that it is earning its keep.
