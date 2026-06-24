# Enrich inline on parser thread; classify queue decouples LLM

`enrich()` moves inline onto the parser thread. Each parser thread: `discover → gates → enrich → gates → enqueue to classify queue`. Classify queue carries `(stub, body, parser_id)` — body fetched, all non-LLM gates passed. Classify workers drain queue and run only the LLM call.

Absorbs former queue topology decision: one shared classify queue (fan-in from parser threads), replacing per-parser inbound queues and the old enrich queue.

## Why

- Thread blocked on ~10-30s LLM round-trip can't dequeue next stub's native fetch. Sequential fetch on parser thread is fast enough; classify queue absorbs latency mismatch.

## Consequences

- Parser threads do `enrich()` I/O (amends ADR-0002). Gates invoked individually on parser thread.
- Classify workers drain classify queue, not enrich queue.
