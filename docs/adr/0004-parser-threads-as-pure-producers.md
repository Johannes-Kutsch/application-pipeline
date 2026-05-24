# Parser threads are pure producers; main thread owns all writes

Each **Parser** runs on its own thread (one per `parser_type`) for concurrent source fetching. Within a thread everything is sequential; across threads, parallel.

Threads are **pure producers**: they run `discover()` and `enrich()` but never touch **Deduplication** or the **Daily Results File**. Writer ownership for `seen.json` and the daily file sharpened by the classify worker's lock (ADR-0028). Amended by ADR-0042: parser threads now perform `enrich()` I/O inline with discovery.

## Why

- Per-host pacing stays inside the parser — one `httpx.Client` per parser; only that thread sleeps on throttle.
- Network and LLM are different bottlenecks. Keeping LLM work off parser threads lets each run at its natural rate.
- Parser interface stays unaware of threading: `discover(query)` / `enrich(stub)`.
- No shared mutable module-level state.

## Consequences

- Thread count = distinct `parser_type` strings in `SOURCES`. No `MAX_PARSER_CONCURRENCY` knob.
- `Parser` Protocol contract: instances must be safe to use from a single dedicated thread.
