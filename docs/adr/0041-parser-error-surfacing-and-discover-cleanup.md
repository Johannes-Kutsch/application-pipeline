# Parser error surfacing; retire `max_results`; remove in-parser dedup; missing title is failure

Four related changes unified by: parser-level problems must be visible, parsers should not duplicate orchestrator responsibilities.

## Changes

1. **`_handle_parser_dead` writes a Failure Report** — today only logs to `run.log`. Report includes parser ID, URL, HTTP status/exception, traceback.
2. **`max_results` retired** — parsers paginate until source returns empty page. Dedup short-circuits downstream cost.
3. **In-parser reference-number dedup removed** (Bundesagentur's `seen: set[str]`). Deduplication Store is sole authority.
4. **Missing title writes a Failure Report** — operator can investigate API schema change. Stub still skipped.

## Why

- Dead parsers and schema changes need operator visibility via Failure Reports, not `run.log`.
- `max_results` was artificial friction — silently truncates broad keywords.
- In-parser dedup duplicates the Deduplication Store for negligible savings.

## Consequences

- Amends ADR-0007 (Failure Reports gain parser-dead and missing-title triggers).
- Amends ADR-0038 (`ParserQuery` loses `max_results`).
- Amends ADR-0004 (parser threads lose in-parser dedup state).
