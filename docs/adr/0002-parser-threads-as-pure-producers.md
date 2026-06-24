# Parser threads: one per parser type, concurrent fetch

Each **Parser** runs on its own thread (one per `parser_type`). Within a thread sequential; across threads parallel. Threads do `discover()` and `enrich()` I/O inline (ADR-0033). No shared mutable module-level state.

## Why

- Per-host pacing stays inside the parser — one `httpx.Client` per parser, only that thread sleeps.
- Network and LLM are different bottlenecks. Keeping LLM work off parser threads lets each run at its natural rate.

## Consequences

- Thread count = distinct `parser_type` strings in `SOURCES`.
- `Parser` Protocol: instances safe to use from a single dedicated thread.
