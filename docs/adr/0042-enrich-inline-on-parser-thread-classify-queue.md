# Enrich inline on parser thread; classify queue decouples LLM

`enrich()` moves inline onto the parser thread, interleaved with discovery. Each parser thread runs: `discover → per-gate checks → enrich → per-gate checks → enqueue to classify queue`. Classify queue carries `_ClassifyRequest(stub, body, parser_id)` — body fetched, all non-LLM gates passed. Classify workers drain the queue and run only the LLM call.

Decouples native fetch from LLM latency — native API load distributes evenly across discovery instead of stalling while Claude thinks. Each stage's parallelism independently controlled.

## Why

- A thread blocked on ~10-30s LLM round-trip can't dequeue the next stub's native fetch. `enriched` counter stalls, native API sits idle.
- Adding threads to speed fetch over-parallelises LLM calls and hits quota walls faster.
- Sequential fetch on parser thread is fast enough; classify queue absorbs latency mismatch.

## Consequences

- Parser threads no longer pure producers (amends ADR-0004) — they now do `enrich()` I/O.
- Gates bundle broken up into individual per-gate calls on parser thread. Post-enrich pre-filter dropped (redundant). Freshness drops from both arms summed into one counter.
- Amends ADR-0006 (enrich queue → classify queue). Amends ADR-0031 (classify-only workers). Amends ADR-0038 (gates bundle retired as single call site).
