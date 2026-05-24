# LLM Enricher replaces per-source `enrich()`; classifier emits Card content

Parsers shrink to `discover(query) -> Iterable[PositionStub]` plus `enrich(stub) -> EnrichResult` (restored by ADR-0038). A shared **LLM Enricher** owns the classify LLM call. Output: `{matches: bool, header: str | None, summary: str | None}` (field renamed by ADR-0034). The classifier-time **Header** is the fully rendered three-line Card top block; **Summary** is the prose paragraph.

`StructuredExtract` (formerly ADR-0022) retires. `extracts.json` becomes `{stable_id: {header, summary}}`. **Match Judge** ranks on Header + Summary directly. `ExternalRedirect` and HTTP-3xx detection retire — redirects followed silently during body fetch (now in `parsers/body_fetch.py` per ADR-0038).

Body strip: per-source CSS selector when present, generic library fallback otherwise. Oversized bodies → stash raw HTML to `.runtime-data/failures/oversized/`, no `seen.json` mark. Malformed LLM output → stash to `failures/malformed/`, no mark, retry next run.

**Freshness Gate** runs at three sites (ADR-0038): post-discover, post-enrich, post-LLM.

## Why

- Parsers were 250–335 lines each, mostly per-source field-extraction heuristics. LLM extracts these more robustly; new source now ~30 lines.
- Aggregator redirects were thrown away under the old ExternalRedirect model.
- "Parsers never guess" invariant was a cheap-model constraint — overruled under capable-model regime.
- Single LLM call subsumes classify + Header/Summary at ~400 extra output chars.

## Consequences

- `Position` dataclass retires (closed-enum fields live inside LLM-authored Header). `raw_description` fed to LLM but never persisted/rendered.
- `Renderer` collapses to `f"# **{rank}:** {header}\n\n{summary}\n"`.
- `PositionStub` gains optional `posted_date`, `company`, `location` pre-fills.

## Supersedes

- Former ADR-0022 (StructuredExtract). Former ADR-0013 (ExternalRedirect). Former ADR-0037 (HTTP 3xx).
