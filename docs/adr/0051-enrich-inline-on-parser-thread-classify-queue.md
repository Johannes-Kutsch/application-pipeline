# Enrich inline on parser thread; classify queue decouples LLM

ADR-0047 restored `Parser.enrich()` as a body-producer but the orchestrator still ran it inside `_EnrichThread` — the same thread pool that runs the LLM classify call. A thread blocked on a ~10-30s LLM round-trip can't dequeue the next stub's native fetch, so the "enriched" counter visibly stalls and the native API sits idle while Claude thinks. The coupling also prevents independent tuning: adding threads to speed up native fetch over-parallelises LLM calls and hits quota walls faster.

Decision: move `enrich()` inline onto the parser thread, interleaved with discovery. Each parser thread runs `discover → per-gate checks → enrich → per-gate checks → enqueue to classify queue`. The classify queue carries a new `_ClassifyRequest(stub, body, parser_id)` — body already fetched, all non-LLM gates already passed. `_EnrichThread` (renamed conceptually to classify workers) drains the classify queue and runs only the LLM call + verdict handling. `claude_classify_parallelism` continues to size the classify worker pool.

Consequence: native API load distributes evenly across discovery (no bursty drain phase), the classify queue absorbs the latency mismatch, and each stage's parallelism is independently controlled. Parser threads are no longer pure producers (they now do I/O on `enrich()` calls), which amends ADR-0005's "parser threads as pure producers" — acceptable because the parser already owns its `httpx.Client` and `ParserHttp` pacing.

## Considered alternatives

- **Keep single stage, add more `_EnrichThread` workers.** Rejected: over-parallelises LLM calls to compensate for fetch starvation. Quota wall fires more often; no independent tuning.
- **Two-stage with a separate fetch thread pool.** Rejected during grilling: native fetch is fast enough to run sequentially. Sharing the parser thread distributes API load evenly across the run instead of bursting fetches after discovery completes.
- **Fetch thread pool with its own parallelism knob.** Rejected: unnecessary complexity — sequential fetch on the parser thread is sufficient and keeps API load smooth.

## Supersedes / amends

- **Amends ADR-0005** (parser threads as pure producers): parser threads now perform `enrich()` I/O inline. They remain the sole owner of their `httpx.Client`.
- **Amends ADR-0007** (orchestrator queue topology): the enrich queue is replaced by a classify queue. Items arrive with body already fetched.
- **Amends ADR-0040** (parallel classify worker pool): `claude_classify_parallelism` now sizes the classify-only worker pool, not the combined fetch+classify pool.
- **Amends ADR-0047** (parser owns body production, gates bundle): the gates bundle is broken up into individual per-gate calls on the parser thread. Post-enrich pre-filter call dropped (redundant — title unchanged by enrich). Freshness drops from both arms summed into one counter.
