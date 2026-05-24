# Parser owns body production; gates bundle interposes

Parsers regain `enrich(stub) -> EnrichResult` — opt-in native path or shared `parsers/body_fetch.py` fallback. `EnrichResult` carries updated stub, body text, and `mode: Literal["native", "fallback"]`.

**LLM Enricher** collapses to: receive `(stub, body)`, run LLM call, run post-LLM Freshness arm, write Card. No httpx client, no body strip, no `body_selector`.

**Gates Bundle** (retired as single call site by ADR-0042) grouped non-LLM gates — Dedup, Pre-Filter, Freshness, Content Gate — now invoked individually by parser thread at their pipeline positions.

Pipeline order: `discover → Freshness → Dedup → Pre-Filter → enrich → Freshness → Content Gate → classify queue → LLM call → post-LLM Freshness → CardStore`.

## Why

- Body quality: Bundesagentur's `/jobdetails` API returns clean structured text; LLM Enricher was ignoring it and re-fetching.
- Cost: gates before enrich avoid body fetch for ~85k dedup-hit stubs per day in steady state.
- Uniform gate site makes pipeline order explicit.

## Consequences

- `Parser` Protocol gains `enrich(stub) -> EnrichResult` (mandatory) and `has_native_enrich: bool` (default `False`). `body_selector` becomes parser-private.
- `EnrichFailedError` raised on unrecoverable HTTP failure (ADR-0039 amends: no `seen.json` write).
- Bundesagentur gains native enrich (`has_native_enrich = True`). Others delegate to `fetch_and_strip`.
- Freshness Gate runs at three sites: post-discover, post-enrich, post-LLM. `gate_arm: "discover" | "post_enrich" | "post_llm"`.
- Amends ADR-0032 (reverses "LLM Enricher owns body fetch"). Amends ADR-0018 (three Freshness arms). Amends ADR-0030 (Content Gate moves to parser thread).
