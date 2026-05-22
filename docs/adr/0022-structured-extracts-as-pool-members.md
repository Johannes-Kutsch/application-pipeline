# Classifier produces structured extracts; pool membership lives on dedup status

> **RETIRED** by ADR-0041. `StructuredExtract` retires; the classifier now emits `{in_domain, header, summary}` where Header is the rendered Card top block and Summary is the prose paragraph. `extracts.json` becomes `{stable_id: {header, summary}}`. Wipe-state migration per ADR-0024 — no auto-translation. The Match Judge ranks on Header + Summary directly; full `raw_description` still never reaches the Judge. Pool-membership-on-dedup-status (`status == in_domain`) and the persisted-sidecar-keyed-by-stable-id pattern survive unchanged.

`RelevanceVerdict` expands from `{in_domain: bool}` to `{in_domain: bool, extract: StructuredExtract | None}`. When `in_domain` is true, `extract` carries a fixed-field representation — `{seniority, work_model, contract_type, key_skills: list[str] (≤10), key_responsibilities: list[str] (≤10), must_have_requirements: list[str] (≤10), notable_caveats: str (≤200 chars)}`. Extracts are persisted in a sidecar (`data/extracts.json`) keyed by a **stable cross-URL identifier** shared across tuple-aliased records. They live as long as the listing stays in the **Pool**, deleted on transition to `selected_by_judge` or `out_of_domain`. The **Match Judge** consumes only extracts — full `raw_description` never enters the judge prompt.

The **Pool** is the implicit set `{url ∈ .seen.json : status == in_domain ∧ parser_re_discovered_today}` — no explicit pool data structure. `SeenResult` becomes 4-variant: `url_hit`, `tuple_hit`, `in_domain` (the new variant — enrich and route directly to today's judge candidates, bypassing classify), `miss`.

Companions: ADR-0020, ADR-0021, ADR-0023, ADR-0024.

## Why

- **Description tokens were paid twice per item.** Classifier reads each `raw_description`, emits a bool, discards the rest. Judge re-reads the same `raw_description`. With items lingering across runs (ADR-0020), judge would re-read on every rediscovery. Structured extract is paid once at classify; persists; reused.
- **The extract carries enough signal for ranking + Card authoring.** Seven fields + free-text `notable_caveats` cover comparison signal and tone (negation: "kein Homeoffice"; "explizit Junior, Deutsch C2 Pflicht"). Judge doesn't need the full description.
- **Pool membership is *already* on the dedup record.** `status == in_domain` ≡ "this URL is in the pool". Re-discovery by a parser is the natural availability check — if the parser doesn't yield the URL today, it's not in today's judge list, regardless of age. No availability HEAD-check, no TTL eviction, no separate index.
- **Sidecar keeps `.seen.json` small.** Extracts are ~300–500 tokens per item. Embedding inflates the file across years; every Syncthing-synced write would carry the bloat.
- **Stable cross-URL key handles syndication.** Tuple-tier alias (ADR-0003) recognizes the same role under two URLs; extract store keys by the shared id so a role's extract is computed once even when re-discovered under a different URL.

## Considered alternatives

- **Embed extracts in `.seen.json`.** Rejected: unbounded growth; dedup store becomes a content cache.
- **Per-item sidecar files (`data/extracts/<id>.json`).** Rejected: many small files play badly with Syncthing.
- **No extract — judge re-reads `raw_description` from a content cache.** Rejected: doesn't solve cross-run re-read cost.
- **Caveman extract (key phrases).** Rejected: loses negations ("kein Homeoffice" → "Homeoffice").
- **Extract on-demand at judge time, no persistence.** Rejected: doesn't survive across rediscovery.
- **Re-classify on each rediscovery to refresh the extract.** Rejected: classifier output is stable across rediscoveries; re-classifying re-pays for no new information.
- **TTL-evict in-domain items.** Rejected for v1: parser-stopped-re-discovering is the natural eviction.

## Consequences

- `StructuredExtract` is a frozen dataclass in `src/application_pipeline/llm/types.py`.
- Classifier prompt template gains "for each in-domain item, also produce the structured extract" section. Output schema: `[{"id": "...", "in_domain": true, "extract": {...}}, {"id": "...", "in_domain": false}, ...]`. `<verdicts>` wrapper unchanged.
- **Extract store** at `data/extracts.json`, shaped `{stable_id: StructuredExtract}`. The classify worker writes eagerly (same fsync discipline as `mark_in_domain`).
- **Stable id**: either canonical-url chasing (record stores `canonical_url`, alias writes point to original) or synthetic id (UUID minted on first-seen). Implementation detail; extract store keys by whichever.
- **Judge call**: `judge_top_n(candidates: list[JudgeCandidate]) -> list[MatchVerdict]` where each candidate carries `id`, stable key, structured extract.
- **Daily file rendering uses full `raw_description`.** `## Job Description` is sourced from the `Position` re-enriched at the day's run, not from the extract.
- **Status transitions**: `mark_in_domain(stub, extract)` writes status `in_domain` + extract; `mark_selected_by_judge(stub)` writes `selected_by_judge` + deletes extract; `mark_out_of_domain(stub)` writes status + (no-op) ensures no extract exists.
- **No back-compat for the old `.seen.json` shape** — per ADR-0024 the migration is "wipe state".
