# Parser owns body production; gates invoked individually

Parsers have `enrich(stub) -> EnrichResult` — native path or shared `parsers/body_fetch.py` fallback. `EnrichResult` carries updated stub, body, `mode: Literal["native", "fallback"]`.

**LLM Enricher** collapses to: receive `(stub, body)`, run LLM call, run post-LLM Freshness arm, write Card. No httpx client, no body strip.

Gates Bundle retired as single call site (ADR-0033). Non-LLM gates invoked individually by parser thread: `discover → Freshness → Dedup → Pre-Filter → enrich → Freshness → Content Gate → classify queue → LLM → post-LLM Freshness → CardStore`.

## Why

- Body quality: native APIs return clean text the LLM Enricher was ignoring. Gates before enrich avoid body fetch for ~85k dedup-hit stubs/day.

## Consequences

- `Parser` Protocol: `enrich(stub) -> EnrichResult` (mandatory), `has_native_enrich: bool` (default `False`).
- `EnrichFailedError` on unrecoverable HTTP failure (ADR-0030: no `seen.json` write).
- Freshness Gate at three sites: post-discover, post-enrich, post-LLM.
