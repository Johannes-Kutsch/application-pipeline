# Native enrich exclusive — no fallback scrape for native-enrich parsers

Parsers declaring `has_native_enrich = True` use their native path exclusively. Native failure or empty body → stub skipped with **Failure Report**. `fetch_and_strip` never called for these parsers. It remains the primary (and only) strategy for `has_native_enrich = False` parsers.

## Why

- Trust the native path. Falling back to scraping masks the failure — operator never learns the API broke.
- Clear failure signal via Failure Report (file the operator must acknowledge), not a warning in the log.
- Separation of concerns: `fetch_and_strip` is designed for scrape-as-primary-strategy sources.

## Error handling for native detail API

- 404/400/422 → skip stub, no Failure Report (per-URL issue).
- 401/403 → Failure Report, parser marked dead.
- Non-retryable 5xx → Failure Report, parser marked dead.
- Retries exhausted → skip stub, no Failure Report.
- 3xx redirect → Failure Report, parser marked dead (API endpoint moved).
- JSON decode error → Failure Report, parser marked dead.
- Empty body → Failure Report, skip stub.

## Consequences

- `EnrichResult.mode` always `"native"` for native parsers. `"fallback"` means scrape-is-primary.
- `body_selector` becomes unused on native-enrich parsers.
- Amends ADR-0038 (reverses native-then-fallback pattern for native parsers).
