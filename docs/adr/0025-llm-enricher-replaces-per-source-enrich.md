# LLM Enricher replaces per-source `enrich()`; classifier emits Card content

Parsers shrink to `discover()` + `enrich(stub) -> EnrichResult` (ADR-0029). Shared **LLM Enricher** owns the classify LLM call. Output: `{matches: bool, header: str | None, summary: str | None}`. Classifier-time **Header** = three-line Card top block; **Summary** = prose paragraph.

`StructuredExtract` retires. `extracts.json` becomes `{listing_id: {header, summary, body}}`. **Match Judge** ranks on Header + Summary directly.

Body strip: per-source CSS selector or generic library fallback. Oversized → stash to `failures/oversized/`, no `seen.json` mark. Malformed LLM output → stash to `failures/malformed/`, retry next run.

**Freshness Gate** at three sites (ADR-0029): post-discover, post-enrich, post-LLM. `ExternalRedirect` retired — redirects followed silently in `parsers/body_fetch.py`.

## Why

- Parsers were 250–335 lines each of per-source heuristics. LLM extracts robustly; new source ~30 lines.
- Single LLM call subsumes classify + Header/Summary at ~400 extra output chars.

## Consequences

- `Position` dataclass retires (fields live in LLM-authored Header). Card = `f"# **{rank}:** {header}\n\n{summary}\n"`.
- `PositionStub` gains optional `posted_date`, `company`, `location` pre-fills.
