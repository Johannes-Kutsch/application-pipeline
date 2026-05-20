# Parser threads are pure producers; main thread owns all writes

Each **Parser** instance runs on its own thread (one per `parser_type`) so multiple sources fetch concurrently without contending on per-host pacing. Within a parser thread everything stays sequential; across parser threads, work is parallel.

Threads are **pure producers**: they run `discover()` *and* `enrich()` but never touch **Deduplication** or the **Daily Results File**. They push **Position Stubs** onto an outbound `queue.Queue` and block on a per-parser inbound queue for the orchestrator's decision. Writer ownership for `.seen.json` and the daily file is sharpened by ADR-0014 (the classify worker also marks the dedup store, behind a lock).

## Why

- **Per-host pacing stays inside the parser.** One `httpx.Client` per parser; only that thread sleeps on `_throttle()`. No shared rate-limiter.
- **Network and LLM are different bottlenecks.** Network is fast (~50–500 ms); Claude is slow (seconds). Putting LLM work off the main loop keeps each pipeline at its natural rate.
- **Parser interface stays unaware of threading.** `discover(query)` / `enrich(stub)` — no queues, no callbacks. The dev script `python -m application_pipeline.parsers.<name>` runs both on the main thread with no orchestrator.
- **No shared mutable state.** Module-level *constants* (e.g. `_DISPLAY_NAME`) and pure functions implementing the **Location Coverage** protocol (per ADR-0012) are allowed — no caches, no global counters, no module-level `httpx.Client`.

## Consequences

- Orchestrator gains a producer/consumer skeleton built on `queue.Queue` and `threading.Thread`.
- `Config` does NOT gain `MAX_PARSER_CONCURRENCY`; thread count = distinct `parser_type` strings in `SOURCES`.
- Back-pressure between threads is implicit: the parser pushes one stub then blocks on the decision queue.
- `Parser` Protocol contract: "instances must be safe to use from a single dedicated thread for the lifetime of the run."
