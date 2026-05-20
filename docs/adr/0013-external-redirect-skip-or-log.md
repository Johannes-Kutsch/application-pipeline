# External-redirect detection: skip-and-log or keep-and-log

When a **Parser** detects that the detail page's body content has been replaced by (or augmented with) an outbound URL to a third-party board, it records one `external_redirect` row to its component event log with a `skipped: bool` field. Two outcomes share the same call site:

- **`skipped=true`** — no usable body. Parser returns `ExternalRedirect(stub, outbound_url)` to the orchestrator; orchestrator marks the stub `external_redirect` in `.seen.json` and bumps the `external_redirects` count in the run-end events row.
- **`skipped=false`** — usable body present alongside the outbound URL. Parser records the outbound URL but returns a normal `Position` for downstream processing. Dedup state proceeds normally; the skip counter is NOT bumped.

`jobs_beim_staat_html` was the first `skipped=true` site; `bundesagentur_api` is the first to exercise `skipped=false` (~10% of items carry an `externeURL` alongside a non-empty `stellenangebotsBeschreibung`).

## Why

- **External listings are a distinct outcome, not a failure.** Conflating into `ParserError`/`enrich_failed` would mean WARNINGs for an expected, recurring case and lose the operator-visible bucket.
- **The information we want to keep is the outbound target.** The events log is staging for the long-term plan: pick which third-party host to implement as the next parser.
- **The "skip" and "keep" outcomes share the same signal but differ on policy.** `ExternalRedirect` (sticky-skip payload) means "nothing to enrich here." Bundesagentur often has a real German description *and* an `externeURL` — skipping it would silently drop ~10% of real listings. Single event name with a `skipped` field; cross-host queries stay one-line.
- **`external_redirects` counter stays narrow.** It answers "how many wrappers did we decline?" — not "how many `externeURL` signals did we see?" The events log already answers the encounter question precisely.
- **Detection is shape-driven, not allowlist-driven.** Allowlists pre-filter exactly the population we want to discover.

## Considered alternatives

- **Raise `ParserError` and add a log line.** Rejected: conflates expected and broken; WARNINGs train the operator wrong.
- **Wrapper-only `Position` with empty body.** Rejected: pays classifier cost for deterministic-zero-value outcome; pollutes `kept`/`off_domain` ratios.
- **Follow the redirect, scrape the third-party page.** Out of scope for v1 — each partner is a parser-sized commitment.
- **Two distinct event names** (`external_redirect` vs `external_partner_link`). Rejected: every cross-host analysis becomes a union of two streams.
- **Skip every `externeURL`-bearing Bundesagentur listing.** Rejected: discards real listings whose savings are imaginary.
- **Skip in `discover` instead of `enrich`.** Rejected: signal lives on the wrapper page, not the list page — would double HTTP cost.

## Consequences

- **`ExternalRedirect(stub, outbound_url)` payload** lives in `parsers/types.py` alongside `Position`/`PositionStub`. Frozen dataclass.
- **Parser hook**: after parsing the detail response, compute the outbound URL (parser-shaped — `<a href>` host check for html parsers; `externeURL` field for bundesagentur_api). If outbound is non-empty:
  - body empty → record event with `skipped=true`; return `ExternalRedirect`.
  - body non-empty → record event with `skipped=false`; return normal `Position`.
- **Orchestrator's `ExternalRedirect` branch**: logs INFO, calls `mark_external_redirect(stub)`, increments `external_redirects` counter. No WARNING.
- **`.seen.json` `status` includes `"external_redirect"`** as a terminal-skip value.
- **Smoke-test guard for `externeURL`** (marked `smoke`, offline by default) catches Arbeitsagentur dropping/renaming the field.
