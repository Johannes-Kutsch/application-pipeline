# Native enrich exclusive — no fallback scrape

Parsers with `has_native_enrich = True` use native path exclusively. Native failure → stub skipped with **Failure Report**. `fetch_and_strip` never called; it remains strategy for `has_native_enrich = False` parsers.

## Error handling for native detail API

- 404/400/422 → skip stub, no Failure Report.
- 401/403 → Failure Report, parser dead.
- Non-retryable 5xx → Failure Report, parser dead.
- Retries exhausted → skip stub, no Failure Report.
- 3xx redirect → Failure Report, parser dead.
- JSON decode error → Failure Report, parser dead.
- Empty body → Failure Report, skip stub.

## Why

- Trust the native path. Falling back to scraping masks failures.

## Consequences

- `EnrichResult.mode` always `"native"` for native parsers. `"fallback"` = scrape-is-primary.
