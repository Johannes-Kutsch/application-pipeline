# Tuple-match writes a URL alias inside `is_seen`

When `is_seen` matches via `(company_lc, title_lc, location_lc)` under a new URL (syndicated copy), the **Deduplication Store** writes an alias entry under the new URL — duplicating `status` and `first_seen` — so subsequent runs short-circuit on the cheap URL lookup. The alias is a transparent side effect of the read.

## Why

- Most stubs expose URL before description. Re-doing tuple lookup every run for a recognized syndicated copy is wasted work.
- Alias-write internal to dedup module avoids a caller-driven `record_alias` footgun.
- `first_seen` copies the original's value — answers "when did this *role* first appear".

## Consequences

- Single-writer Pi (ADR-0002) makes a side-effecting query safe.
- `is_seen` may raise `OSError` from the alias write.
