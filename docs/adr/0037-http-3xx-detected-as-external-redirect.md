# HTTP 3xx during `enrich()` is a second detection shape for External Redirect

> **RETIRED** by ADR-0041. Per-parser `enrich()` retires; body fetch moves to the shared **LLM Enricher** which follows redirects silently via `httpx`'s default redirect handling. No host-classification, no `ExternalRedirect` payload, no parser-side fatal/non-fatal distinction. Destinations with no usable body are caught by **Content Gate**; oversized destinations are caught by the token cap and stashed.

When a **Parser**'s `enrich()` call receives an HTTP 3xx response (`301`, `302`, `303`, `307`, `308`), the **ParserHttp** layer surfaces the response to the parser instead of crashing the parser thread. The parser classifies by the `Location` header's host:

- **Off-host `Location`** — parser returns `ExternalRedirect(stub, location_url)`. Orchestrator marks `external_redirect`, bumps the counter (ADR-0013).
- **Same-host `Location`** — parser raises `ParserError` → `mark_enrich_failed`. Terminal per-stub skip; parser continues.
- **Missing / malformed `Location`** — same as same-host: `ParserError` → `mark_enrich_failed`.

This is a second detection shape for ADR-0013's External Redirect outcome — the first being the in-body `<a href>` / `externeURL` scan. Policy is unchanged; only the signal channel is new. Applies on `enrich()` only — `discover()` 3xx remains parser-fatal (a redirected search endpoint is not an External Redirect; there is no stub yet).

All three v1 parsers handle the new signal uniformly. `httpx.Client` keeps `follow_redirects=False` so the 3xx surfaces to the parser layer rather than being silently chased.

## Why

- **The incident the ADR addresses.** A 302 from `jobs-beim-staat.de/jobangebote/<id>` killed the `parser_jobs_beim_staat_html` worker mid-run (1 of 79 stubs enriched, 20 of 72 queries served) because `ParserHttp._classify_failure` bucketed 3xx into "unexpected → fatal". The body-scanning `_find_outbound_href` fallback never ran because no HTML was returned.
- **ADR-0013's policy already covers this.** "External listings are a distinct outcome, not a failure" — a 302 off the source's host is exactly that outcome. Conflating into `HttpParserFatalError` train-wrecks the parser on what should be a routine skip.
- **Detection stays shape-driven, not allowlist-driven.** Each parser maps the new HTTP-level signal itself, mirroring how `_find_outbound_href` (HTML) and `externeURL` (JSON) differ today. The HTTP layer is generic; the policy stays parser-local.
- **Off-host vs same-host is the only consequential distinction.** The specific 3xx code doesn't change the semantic; a follow-once policy for same-host 302s adds speculative complexity for a case we have no evidence of. If a future source canonicalises via same-host 302, the `parser_http.events.jsonl` log will name it precisely and we revisit then.
- **Discover scope stays narrow.** A redirect on a search endpoint is a different problem class (the source is restructuring URLs or rate-limiting via redirect) and shouldn't be silently absorbed as an External Redirect — there's no stub yet to attach the outbound URL to.
- **All three parsers, one rule.** Each parser routes `ParserHttp.get()` through the same surface; tasking only `jobs_beim_staat_html` leaves the latent bug live in the other two. The marginal code is a uniform `try`/`except` at the `enrich()` call site.

## Considered alternatives

- **Generic HTTP-layer policy (ParserHttp returns `ExternalRedirect` directly based on host comparison).** Rejected: bakes a host-comparison heuristic into the generic layer and removes parser-level discretion. ADR-0013 deliberately keeps redirect detection parser-shaped — a future first-party-only parser may want a different rule.
- **Treat all 3xx as `HttpStubNotRetryableError` / `mark_enrich_failed`.** Rejected: conflates expected partner-link redirects with broken listings, under-reports the `external_redirects` counter, and loses the outbound URL from the events log — the exact failure mode ADR-0013 was written to prevent.
- **Set `httpx.Client(follow_redirects=True)` and let the client chase 3xx.** Rejected: the parser would land on third-party HTML it isn't built to scrape and downstream filters would see a successful response with foreign content. Loses the boundary signal entirely.
- **Follow-once policy for same-host 302s (canonicalisation tolerance).** Rejected: no evidence the case occurs on any v1 source; adds follow-count state and an extra GET for a hypothetical. If it shows up, the log names it and we revisit.
- **Only 302, enumerate other 3xx codes as they appear.** Rejected: zero cost to cover the whole 3xx range with one classifier branch, and the alternative is signing up for the same incident under 301/307/308.
- **Bundle a broader "narrow the `HttpParserFatalError` bucket" change.** Deferred to a separate issue. That work changes the documented error semantics for *every* parser-fatal path (auth, 5xx) and deserves its own ADR + PR. Bundling muddies the rollback story for the 3xx fix.

## Consequences

- **`ParserHttp` classification table grows one branch.** 3xx no longer falls through to the `unexpected → fatal` line; the HTTP layer exposes the response (status + `Location` header) to the caller without raising `HttpParserFatalError`. Exact mechanism (new exception type carrying the `Location`, vs a new return shape) is implementation detail.
- **Each `enrich()` call site gains a uniform classification hook.** Compares `Location` host against the parser's source host; off-host → `ExternalRedirect`; otherwise → `ParserError`. The body-scanning `_find_outbound_href` / `externeURL` paths are unchanged — they still fire when a 2xx body carries an outbound URL.
- **`stellen_hamburg_api` gains an `ExternalRedirect` return path** even though no redirect has been observed from that source. Cost is one `import` and one `except` clause; benefit is consistent parser contract.
- **`parser_http.events.jsonl` gains a `http_get_redirect` event** (or equivalent) so off-host vs same-host 3xx is visible without re-reading source HTML. The existing `http_get_skipped` / `http_get_fatal` events stay.
- **CONTEXT.md's `mark_*` error-semantics paragraph** gains a 3xx line; the existing `4xx → enrich_failed` and `auth/5xx → HttpParserFatalError` lines stay verbatim.
- **No `.seen.json` migration.** `external_redirect` and `enrich_failed` are existing statuses; the change only widens the set of inputs that route to them.
- **Smoke tests** for each parser gain a 3xx-handling case (mocked `_http_get` returning a sentinel) — off-host → `ExternalRedirect`, same-host → `ParserError`. Prior art: the existing 4xx-classification tests in `tests/parsers/`.
