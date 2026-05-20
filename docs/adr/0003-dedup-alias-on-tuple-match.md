# Tuple-match writes a URL alias inside `is_seen`

When `is_seen` matches via the `(company_lc, title_lc, location_lc)` tuple under a *new* URL (syndicated copy), the **Deduplication Store** internally writes an alias entry under the new URL — duplicating the original record's `status` and `first_seen` — so subsequent runs short-circuit on the cheap URL lookup. The return value (the `SeenResult` variant) is unaffected; the alias is a transparent side effect of the read.

## Why

- **Most stubs expose URL before description.** Re-doing tuple normalisation + lookup on every run for an already-recognised syndicated copy is repeated work.
- **Alias-write internal to dedup module.** A caller-driven `record_alias` would force every `is_seen` call site to remember a follow-up — a footgun. Folding the write into `is_seen` removes it.
- **On-disk shape stays flat.** Each entry is self-describing `{url: record}`; no sum type on disk.
- **`first_seen` semantics stay correct.** Alias copies the *original*'s `first_seen` — the paper trail answers "when did this *role* first appear".

## Consequences

- Single-writer Pi (ADR-0002) means a side-effecting query is safe.
- `is_seen` may raise `OSError` from the alias write; the orchestrator's top-level `mark_*` handler covers it.
- Future fuzzy-match upgrades on the `_tuple_lookup` seam inherit the alias-write automatically.
- `is_seen`'s docstring must call out the side effect.
