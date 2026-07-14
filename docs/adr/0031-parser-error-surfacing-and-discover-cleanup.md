# Parser error surfacing; retire `max_results`; remove in-parser dedup; missing title is failure

1. **`_handle_parser_dead` writes a Failure Report** — includes parser ID, URL, HTTP status/exception, traceback.
2. **`max_results` retired** — parsers paginate until source returns empty page.
3. **In-parser dedup removed** (Bundesagentur's `seen: set[str]`). Deduplication Store is sole authority.
4. **Missing title writes a Failure Report** — operator investigates API schema change.

## Why

- Dead parsers and schema changes need operator visibility via Failure Reports, not `run.log`. `max_results` silently truncates broad keywords. In-parser dedup duplicates the store.
