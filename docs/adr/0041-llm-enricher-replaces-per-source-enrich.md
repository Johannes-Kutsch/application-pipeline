# LLM Enricher replaces per-source `enrich()`; classifier emits Card content

Per-source **Parsers** retire their `enrich()` method. Each parser shrinks to `discover(query) -> Iterable[PositionStub]` plus an optional `body_selector: str | None` (single CSS selector identifying the job-body container on the source's detail pages). A new shared pipeline stage, **LLM Enricher**, owns body fetch + field extraction + relevance classification in a single LLM call. Output shape becomes `{in_domain: bool, header: str | None, summary: str | None}` (header/summary non-None iff in-domain). The classifier-time **Header** is the fully rendered three-line Card top block; the **Summary** is the prose paragraph beneath it.

`StructuredExtract` (ADR-0022) retires. `extracts.json` becomes `{stable_id: {header, summary}}`. The **Match Judge** ranks on Header + Summary directly — no structured comparison fields. `ExternalRedirect` (ADR-0013) and the HTTP-3xx-as-redirect detection (ADR-0037) retire — the **LLM Enricher** follows redirects silently during body fetch and feeds whatever HTML lands on the other side through the same strip-and-extract path.

Body strip is per-source CSS selector when present, generic library fallback (`trafilatura` / `readability-lxml`) otherwise. The generic fallback is what makes redirect-following safe: aggregator destinations are arbitrary employer sites with no known structure. After strip, a hard token cap applies — bodies over cap are dropped, the **raw HTML** is stashed to `<settings-dir>/failures/oversized/<source>-<url-slug>.html`, and `.seen.json` is **not** marked (URL is re-checked next run, mirroring the **Content Gate** pattern). Malformed LLM outputs (empty `header` or `summary` when `in_domain: true`, missing tag, bad JSON) follow the same pattern: stash to `failures/malformed/<source>-<url-slug>.txt`, no `.seen.json` write, retry next run.

The **Freshness Gate** (ADR-0025) runs twice — once after `discover()` when the parser pre-filled `PositionStub.posted_date` (cheap pre-LLM drop), once after the **LLM Enricher** for items where `posted_date` had to be inferred by the LLM. `PositionStub` schema gains `posted_date: date | None` and may optionally gain `company`/`location` pre-fills (parser pre-fills what it cheaply knows from the discover index; LLM fills the rest).

## Why

- **Authoring cost dominated parser work.** Three existing parsers run 250–335 lines each; the majority is per-source field-extraction heuristics (`seniority`, `work_model`, `contract_type`, `salary`, `posted_date` parsing). A capable LLM extracts these from body text more robustly than per-source CSS selectors, and a new source now takes ~30 lines of `discover()` instead of 250. Direct cause of **#523** being mooted.
- **Aggregator redirects were thrown away.** Under ADR-0013, an `ExternalRedirect` ends the listing at the dedup store with no body ever fetched. Aggregators (`stellen.hamburg`, `jobs-beim-staat` to a lesser extent) link out to actual employer postings via redirect, so every aggregator hit lost the content. Following redirects + generic strip + LLM extraction recovers these.
- **The "parsers never guess" invariant was a cheap-model constraint.** CONTEXT.md's parser contract forbade inference (`None` when source exposes no signal — parsers never guess). That rule existed because per-source string-extraction heuristics were brittle. With a capable model, body-driven inference is reliable enough to be acceptable; the user explicitly overruled the invariant during the 2026-05-22 grilling session.
- **Single LLM call subsumes two responsibilities at no extra cost.** Today's classify call already reads `raw_description` and produces output tokens. Adding Header + Summary to the same call adds ~400 output chars; subprocess spin-up cost (the dominant per-call overhead per ADR-0036) is paid once instead of twice.
- **Layout coupling was the cause of #523's confusion.** `StructuredExtract` was simultaneously the Judge's comparison surface and an aspirational source of Card placeholders. Splitting "what the Judge ranks on" from "what the Card renders" collapses the surface — there's only one persisted blob (Header + Summary), and both consumers read it directly.

## Considered alternatives

- **Keep per-source `enrich()`, just follow aggregator redirects manually.** Rejected: solves redirect loss but not authoring/coverage/duplication. Each parser still ships 250+ lines of field heuristics; new sources still expensive.
- **Two-call model: cheap classify on title-only, then full extraction on in-domain only.** Rejected: doubles subprocess overhead on in-domain items (ADR-0036 retired batching for exactly this reason). The single-call short-circuit (`{"in_domain": false}` returned early) already skips extraction work on out-of-domain items.
- **Keep `StructuredExtract` as a sidecar for Judge eyes, free-text Header + Summary for Card.** Rejected during grilling: the capable model can rank on Header + Summary directly; the structured fields were a cheap-model habit. Saves one schema, one persistence path, one prompt section.
- **Pure generic body strip (no per-source selectors).** Rejected: known sources have known main-content containers; using them avoids `trafilatura` misfires on layouts the library wasn't tuned for. Per-source selector is 3 lines, not 250.
- **LLM-driven body stripping** (send full HTML, let the model find the job content). Rejected: defeats the cost rationale of stripping; multiplies input tokens.
- **Truncate oversized bodies silently.** Rejected: silent truncation is lossy in unknown ways. Fail loud + stash raw HTML lets the operator see exactly what was too big and decide (better selector, library override, source-specific fix).
- **Mark oversized URLs `enrich_failed` (terminal).** Rejected: oversized usually means the strip didn't work, not that the listing is bad. Re-check pattern matches **Content Gate** — the bug may get fixed before the next run.
- **Embed Header + Summary inside `.seen.json`.** Rejected for the same reason ADR-0022 rejected embedding extracts: unbounded growth of the dedup file, every Syncthing-synced write carrying the bloat. Sidecar `extracts.json` stays.
- **Freshness Gate runs once, post-enrich only.** Rejected: pays LLM cost on listings the source already dated as stale on its index page. Two-arm gate uses the cheap signal when available.

## Consequences

- **`Parser` Protocol shrinks**: `discover(query) -> Iterable[PositionStub]`, `body_selector: str | None` (module-level constant or class attribute), plus the existing **Location Coverage** Protocol surface. `enrich()` method removed. Parsers no longer construct `Position`.
- **`PositionStub` schema gains** `posted_date: date | None`; may optionally carry `company: str | None` and `location: str | None` pre-fills when the discover index exposes them cheaply (existing fields, now used as Header pre-fills).
- **`Position` dataclass retires**, along with its closed-enum fields (`salary`, `contract_type`, `employment_type`, `work_model`, `deadline`). Their content lives inside the LLM-authored Header string. `raw_description` is fetched + stripped + fed to the LLM but never persisted and never rendered into a Card.
- **New module `llm_enricher.py`** owning: generic HTTP fetch with silent redirect-following, per-source-selector-or-library body strip, token cap with raw-HTML stash on overflow, LLM call, output validation, malformed stash. Construction takes the `LLMExtractor` Protocol implementation, the **Quota Wall**, the parser registry (for `body_selector` lookup), and `RunLog`. Replaces the per-parser `enrich()` call site in the orchestrator.
- **`LLMExtractor` Protocol updated**: `classify_relevance(item: ClassifyItem) -> tuple[RelevanceVerdict, CallUsage]` returns `RelevanceVerdict{in_domain: bool, header: str | None, summary: str | None}`. `StructuredExtract`, `MatchVerdict.matched/missing`, and the `extract` field on `RelevanceVerdict` are removed. `judge_top_n` returns `MatchVerdict{id, rank}` only (no matched/missing/summary — the rendered summary is the classify-time one, retrieved from `extracts.json` at render time).
- **`extracts.json` migrates** to `{stable_id: {header: str, summary: str}}`. Wipe-state migration per ADR-0024 pattern — no auto-translation from the prior `StructuredExtract` shape.
- **`Renderer` collapses** to a one-line concatenation: `f"# **{rank}:** {header}\n\n{summary}\n"`. No placeholder substitution, no `str.format_map`, no `Layout` argument.
- **`Card` glossary entry** in CONTEXT.md is rewritten to describe the fixed two-block structure (`# **{rank}:** Header`, then Summary). Placeholder vocabulary retires.
- **`Freshness Gate` runs twice** — `admit_stub(stub) -> bool` post-discover, `admit(position_like) -> bool` post-LLM-enricher. Per-position transcript gains a `gate_arm: "discover" | "post_enrich"` field. The pre-LLM arm drops cost is the savings; the post-LLM arm catches LLM-extracted dates the parser couldn't pre-fill.
- **`ExternalRedirect` payload, the `external_redirect` `SeenStatus`, and the `external_redirects` run counter all retire.** The redirect-follow happens silently inside body fetch; if the destination has no usable body, **Content Gate** drops it.
- **`Content Gate` stays unchanged** in shape (drops empty stripped body, no `.seen.json` write, transcript-only). Its position in the pipeline shifts from post-`enrich()` to post-strip-inside-LLM-Enricher.
- **`failures/oversized/` and `failures/malformed/` directories** are new; cleanup is manual (delete file = acknowledge, same as **Failure Report**). Neither triggers a Failure Report — both are per-listing soft failures that the next run retries.
- **Token cap value** is deferred to implementation tuning (~8k as an opening guess).
- **Library choice** (`trafilatura` vs `readability-lxml` vs homegrown) is deferred to implementation. `trafilatura` is the default expectation given its multi-language and main-content heuristics, but the integration boundary stays small so the choice is replaceable.
- **Existing parsers** (`bundesagentur_api.py`, `stellen_hamburg_api.py`, `jobs_beim_staat_html.py`) shrink in place: keep `discover()`, add `body_selector` constant, delete `enrich()` and all field-extraction helpers. The per-source `_text.py` normalization helpers retire — generic strip owns this.
- **Test suite**: per-parser `enrich()` integration tests retire. New tests cover the `LLMEnricher` against fixture HTML pages (known-good, oversized, malformed). Offline-by-default per the standing rule (live network gated behind `@pytest.mark.smoke`).
- **Companion ADR-0042** covers the parallel retirement of `layout.py` / `Layout` / placeholder vocabulary that this ADR's hardcoded Card structure forces.
- **Companion follow-up issue #524** tracks the Judge-at-scale concern this redesign surfaces — without `StructuredExtract`'s compressed comparison surface, Pools approaching 500 items challenge Judge ranking quality and cost.

## Supersedes / amends

- **Supersedes ADR-0022** (StructuredExtract as Judge input).
- **Supersedes ADR-0013** (ExternalRedirect skip-or-log).
- **Supersedes ADR-0037** (HTTP 3xx detected as ExternalRedirect).
- **Amends ADR-0025** (Freshness Gate runs twice, not once).
- **Amends ADR-0036** (classifier output shape: `{in_domain, header, summary}`, not `{in_domain, extract}`).
- **Amends ADR-0014** consequence chain through its supersession by ADR-0036 (no further direct effect).
