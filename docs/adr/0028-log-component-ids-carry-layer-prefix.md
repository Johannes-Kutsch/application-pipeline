# Log component IDs carry a layer prefix

> **Refines [ADR-0024](0024-log-convention-split-streams.md) and [ADR-0025](0025-llm-call-sites-are-log-components.md):** the "one file per component" rule still holds; this ADR adds a `<layer>_` prefix to every component identifier so the listing in `data/logs/` groups by layer when sorted.

Every log component identifier carries a layer prefix: `parser_`, `llm_`, or `pipeline_`. The prefix is part of the identifier itself, not a filename-only decoration — it appears in the `<comp>.events.jsonl` / `<comp>.transcripts.jsonl` filename, the `component` field of `lifecycle.jsonl` rows, the `=== <component> ===` header in `run.log`, and the row label in the **Status Display**. ADR-0025's `classify_relevance` and `judge_match` become `llm_classify_relevance` and `llm_judge_match`; parser components become `parser_bundesagentur_api`, `parser_stellen_hamburg_api`, `parser_jobs_beim_staat_html`, `parser_http`; the pipeline-internal components become `pipeline_orchestrator`, `pipeline_dedup`, `pipeline_prefilter`. The shared aggregate files `lifecycle.jsonl` and `run.log` are unprefixed — they are not component files.

## Why

- **`ls data/logs/` was hard to scan.** Flat per-component filenames meant parser logs, LLM logs, and pipeline-internal logs interleaved alphabetically. With three parsers, two LLM call sites, and three pipeline-internal components, "show me only the LLM streams" required eyeballing the list. A layer prefix makes the group structure visible at sort order, with no directory traversal.
- **Symmetric prefixing beats partial prefixing.** Prefixing only the multi-member groups (`parser_`, `llm_`) and leaving singletons bare (`orchestrator`, `dedup`, `prefilter`) was considered. Rejected: singletons today may grow tomorrow (a future second pipeline-internal component would have to be retrofitted), and the asymmetry forces a reader to remember which groups are prefixed. Prefixing every component is the simpler rule.
- **The prefix lives in the identifier, not just the filename.** Keeping `component_id="classify_relevance"` internally and mapping to `llm_classify_relevance.events.jsonl` via a group-lookup table was considered. Rejected: the lookup table is a parallel source of truth that has to stay in sync with the set of components — exactly the kind of split ADR-0024/25 fought to eliminate. With the prefix baked into the id, every site that emits the name (lifecycle field, run.log header, status display) is uniformly prefixed for free.

## Alternatives rejected

- **Subdirectories instead of prefixes** (`data/logs/parser/bundesagentur_api.events.jsonl`). Rejected: forces every `jq` reader, every `ls data/logs/`, and Syncthing-side scripts to learn about a new directory level for no readability gain over a sorted-flat listing.
- **Use `core_` or `run_` for the pipeline-internal group.** Rejected: `core_` is vague; `run_` collides with the existing `run.log` aggregate filename and would visually pair `run_orchestrator.events.jsonl` with the unrelated `run.log`.
- **Auto-rename old log files on startup.** Rejected: this is a personal pipeline with one operator; a permanent startup-path rename pass adds edge cases (collisions with future renames) for a one-time cutover. Old files are left to rot in `data/logs/`; the operator deletes them by hand when convenient.

## Consequences

- The module-level `_COMPONENT_ID` constants in `src/application_pipeline/llm/claude.py` and the corresponding `LLMCallSite` records use the prefixed names.
- Every parser module's `parser_log.record(...)` call site uses the prefixed name, as does every orchestrator/dedup/prefilter call site.
- `CONTEXT.md` updates the **Log Artifacts**, **Relevance Classifier**, **Match Judge**, **Domain Pre-Filter**, **Deduplication**, and **Status Display** entries to use the prefixed identifiers.
- Existing unprefixed files in `data/logs/` on the Pi are not migrated; new runs write to the new names alongside them. The operator removes the stale ones manually.
