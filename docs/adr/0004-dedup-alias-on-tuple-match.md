# Tuple-match writes a URL alias inside `is_seen`

When the **Deduplication Store**'s `is_seen` finds a match via the lowercased `(company, title, location)` tuple under a *new* URL (a syndicated copy of a role first seen under a different URL), it internally writes an **alias entry** under the new URL — duplicating the original record's `status` and `first_seen` — so that subsequent runs short-circuit on the cheap URL lookup without re-checking the tuple. The `is_seen` call's return value is unaffected by this side effect.

## Why

- **Most stubs expose the URL before the description.** Parsers that hit list pages get the URL as the first cheap field; checking the URL is a single dict lookup. Re-doing the tuple normalisation + lookup on every run for an already-recognised syndicated copy is repeated work for no new information.
- **The alias-write is internal to the dedup module.** The orchestrator does not need to know that an alias was written — `is_seen` returns the tier that matched (per ADR-0008) for metrics, but routing the alias-persist through the caller would force a `record_alias` second method that every caller must remember to invoke. Keeping the side effect inside `is_seen` removes that footgun.
- **On-disk shape stays flat.** The write is an index optimization that does not change the answer (any later `is_seen` returns a hit either way). The store remains `{url: record}`; every entry is self-describing.
- **`first_seen` semantics stay correct.** The alias copies the *original*'s `first_seen`, so the paper trail still answers "when did this *role* first appear, under which URL", not "when did this URL first appear".

## Considered alternatives

- **Don't record the alias — accept the tuple-lookup cost on every appearance.** Rejected: defeats the cheap-URL-lookup property the parser API was designed to enable.
- **Caller-driven alias persistence (`is_seen` is pure-read; orchestrator calls a separate `record_alias` on tuple hit).** Rejected: every `is_seen` caller would have to remember the follow-up call. Folding the write into `is_seen` is the safe shape; the return value (per ADR-0008) still carries which tier matched, so the metrics layer can count tuple hits without the caller persisting anything.
- **Indirection on disk (`{url: {alias_of: <canonical_url>}}`).** Rejected: turns the on-disk shape into a sum type, hurts `git log -p` readability for marginal byte savings.

## Consequences

- The dedup module is single-writer (Pi only, per ADR-0002), so a side-effecting query is safe — there is no concurrent-reader correctness concern.
- `is_seen` may raise `OSError` on filesystem failure during the alias write. The orchestrator's top-level handler already catches OS errors from `mark_seen`; the same handler covers `is_seen` for the same reason.
- A future fuzzy-match upgrade (the `_tuple_lookup` seam) inherits the alias-write behaviour automatically — anything that returns a matched canonical URL gets aliased under the new URL on first hit.
- The "pure read" expectation a casual reader might bring to `is_seen` is broken. The module's docstring and `is_seen`'s docstring must call this out so future readers do not "fix" it back to a pure read and reintroduce the per-run tuple-lookup cost.
