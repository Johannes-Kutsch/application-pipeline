# Native enrich exclusive — no fallback scrape for native-enrich parsers

Parsers declaring `has_native_enrich = True` use their native enrich path exclusively. If the native path fails or produces an empty body, the stub is skipped with a **Failure Report**. The shared `fetch_and_strip` fallback helper is never called for these parsers.

`fetch_and_strip` remains the primary (and only) enrich strategy for parsers with `has_native_enrich = False` — it is not a fallback for them, it is the implementation.

## Why

- **Trust the native path.** If a parser has a dedicated API for body production, that API is the authoritative source. Falling back to scraping the public HTML page when the API fails masks the failure — the operator never learns the API broke, and the scraped body is lower fidelity than what the API would have returned.
- **Clear failure signal.** A native enrich failure means something changed — the API endpoint moved, auth expired, the response schema broke. The operator needs to know so they can fix the parser. Silently falling back to a scrape hides this signal behind degraded-but-functional output.
- **Separation of concerns.** `fetch_and_strip` is designed for sources where scraping the public page *is* the enrich strategy (jobs-beim-staat, stellen.hamburg). Using it as a safety net for API-backed parsers conflates two different body-production models and complicates error reasoning.

## Considered alternatives

- **Keep fallback, but log a warning.** Rejected: a warning in the log is easy to miss. The operator needs a Failure Report — a file they must acknowledge — to ensure the API regression is investigated.
- **Keep fallback, but write a Failure Report alongside.** Rejected: if the stub proceeds through the pipeline with a fallback-scraped body, the Failure Report becomes informational noise rather than an actionable signal. The operator may deprioritise it since "the pipeline still works."
- **Remove `fetch_and_strip` entirely.** Rejected: it is the correct enrich strategy for parsers without a native API. Only its role as a fallback for native-enrich parsers is removed.

## Consequences

- **Bundesagentur parser's `enrich()` removes the fallback `fetch_and_strip` call.** The `try/except/pass` + fallback pattern is replaced by: native API call succeeds → return result; native API call fails → raise or skip with Failure Report; native API returns empty body → skip with Failure Report.
- **Empty `stellenangebotsBeschreibung`** (missing or empty string after strip) is treated as a failure: the parser writes a Failure Report and skips the stub. The `or ""` silent fallback to empty string is removed.
- **Error handling for native detail API calls** follows the table agreed during grilling:
  - 404/400/422 → skip stub, no Failure Report (per-URL issue, not parser-level).
  - 401/403 → Failure Report, parser marked dead for this run.
  - Non-retryable 5xx → Failure Report, parser marked dead.
  - Retries exhausted → skip stub, no Failure Report (transient).
  - 3xx redirect → Failure Report, parser marked dead (API endpoint moved).
  - JSON decode error → Failure Report, parser marked dead (structural API change).
- **`EnrichResult.mode` field**: for native-enrich parsers the mode is always `"native"`. The `"fallback"` mode is only produced by parsers whose `enrich()` delegates to `fetch_and_strip` as their primary strategy.
- **`body_selector` on native-enrich parsers becomes unused.** It was only consumed by the fallback helper. Can be removed from parsers that declare `has_native_enrich = True`.
- **CONTEXT.md updates**: `EnrichResult` entry clarifies that `mode="fallback"` means "scrape is the primary strategy", not "native failed". `Parser` entry clarifies the exclusive native enrich model. `Content Gate` entry's note about "native enrich paths return non-empty by construction or raise to `enrich_failed`" updates to reference Failure Reports instead.

## Supersedes / amends

- **Amends ADR-0047**: reverses the "parser's `enrich()` tries native then falls back to shared helper" pattern for native-enrich parsers. The shared helper remains available to non-native-enrich parsers unchanged.
