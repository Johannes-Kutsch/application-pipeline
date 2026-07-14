# Parser owns body production; enrich inline on parser thread

Parsers have `enrich(stub) -> EnrichResult` — native path or shared `parsers/body_fetch.py` fallback. `EnrichResult` carries updated stub, body, `mode: Literal["native", "fallback"]`. `enrich()` runs inline on the parser thread. Each parser thread: `discover → gates → enrich → gates → enqueue to classify queue`. One shared classify queue (fan-in) carries `(stub, body, parser_id)`.

**LLM Enricher** collapses to: receive `(stub, body)`, run LLM call, run post-LLM Freshness arm, write Card. No httpx client, no body strip. Non-LLM gates invoked individually by parser thread: `discover → Freshness → Dedup → Pre-Filter → enrich → Freshness → Content Gate → classify queue → LLM → post-LLM Freshness → CardStore`.

## Why

- Native APIs return clean text the LLM Enricher was ignoring. Gates before enrich avoid body fetch for ~85k dedup-hit stubs/day.
- Thread blocked on ~10-30s LLM round-trip can't dequeue next stub's native fetch. Sequential fetch on parser thread is fast enough; classify queue absorbs the latency mismatch.

## Consequences

- `Parser` Protocol: `enrich(stub) -> EnrichResult` (mandatory), `has_native_enrich: bool` (default `False`). Amends ADR-0002.
- `EnrichFailedError` on unrecoverable HTTP failure (ADR-0029: no `seen.json` write).
- Freshness Gate at three sites: post-discover, post-enrich, post-LLM.
- Classify workers drain classify queue, not enrich queue.
