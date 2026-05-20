# Log convention: split by reader, components are call sites, layer-prefixed

The `data/logs/` directory is laid out by *who reads what*, not by which component emitted it. Four file types:

- **`<comp>.events.jsonl`** — one structured row per step (`{ts, event, ...fields}`, free-form keys). Read by `jq`. One file per component. The run-end metrics row lands here on `pipeline_orchestrator` as `event=run_complete`.
- **`lifecycle.jsonl`** — single shared file carrying status-display register / phase_changed / removed events.
- **`run.log`** — single shared file carrying tracebacks and end-of-run `SUMMARY OF SESSION` blocks, chronologically interleaved with `=== <component>  <ts>  <kind> ===` headers. Read by human eyeballs.
- **`<comp>.transcripts.jsonl`** (LLM components only) — full prompt/response payloads.

**For LLM code, "component" is the call site, not the implementation class.** `llm_classify_relevance` and `llm_judge_match` each own their pair of files; there is no `claude_extractor.*` stream. A row's home is the call site it concerns, regardless of which module wrote it.

**Every component identifier carries a layer prefix**: `parser_` (e.g. `parser_bundesagentur_api`, `parser_jobs_beim_staat_html`, `parser_http`), `llm_` (e.g. `llm_classify_relevance`, `llm_judge_match`), or `pipeline_` (e.g. `pipeline_orchestrator`, `pipeline_dedup`, `pipeline_prefilter`, `pipeline_freshness`). The prefix is part of the identifier, not just the filename — it appears in `lifecycle.jsonl` `component` fields, `run.log` headers, and **Status Display** row labels. The shared aggregates `lifecycle.jsonl` and `run.log` are unprefixed.

The **Run Divider** is retired (per ADR-0021); per-call-site Claude metrics live as fields on the `run_complete` row (e.g. `classify_input_tokens`, `classify_cost_usd`, `judge_input_tokens`, `judge_cost_usd`, `dedup_url_hits`, `dedup_tuple_hits`, `elapsed_s`).

## Why

- **Empty out the chatter.** Previously every component had its own `.log` even if its only on-disk emissions were status-display events. Funnelling lifecycle into one file stops the ghost files.
- **Make per-step events analyzable.** `<comp>.events.jsonl` lets a `jq` pipeline answer "how many `query_started` did Bundesagentur emit at each location" without grepping unstructured text.
- **Eyeball-mode artifacts in one place.** Tracebacks and end-of-run summaries land in `run.log` with attribution headers.
- **"What was classify doing vs judge" is the dominant question.** Different models, different effort levels, different cost profiles, different failure modes. Filing by call site makes the filter the filename.
- **`ls data/logs/` was hard to scan.** Layer prefixes give visible group structure at sort order with no directory level.
- **The prefix in the identifier (not just the filename)** avoids a parallel mapping table that drifts.

## Considered alternatives

- **Single global `events.jsonl` firehose.** Rejected: per-component greppability is the dominant use case.
- **Fold transcripts into events.** Rejected: large prompt/response strings bloat the analysis stream; different readership.
- **One file per component where component = `claude_extractor`.** Rejected: recovering "what did classify cost" from a mixed stream is strictly worse.
- **Subdirectories instead of prefixes.** Rejected: forces every reader to learn a directory level for no readability gain over sorted-flat.
- **`core_`/`run_` for the pipeline-internal group.** Rejected: `core_` is vague; `run_` collides visually with `run.log`.
- **Auto-rename old log files on startup.** Rejected: edge-case-y for a one-time cutover; operator deletes by hand.

## Consequences

- `parser_log` writes structured-event rows to `<comp>.events.jsonl` and lifecycle rows to the shared `lifecycle.jsonl`. `record_traceback` and `summarize` write into `run.log` with `=== <component>  <ts>  <kind> ===` headers.
- Status-display register / phase_changed / removed events route to `lifecycle.jsonl`, not the per-component log.
- `ClaudeExtractor._invoke` routes success-path writers to `site.component_id` (matching what the failure path already does). The `_COMPONENT_ID` module-level constant is removed. `claude_extractor.events.jsonl` / `claude_extractor.transcripts.jsonl` are no longer produced.
- Existing unprefixed/legacy files in `data/logs/` are not migrated — new runs write to the new names alongside them.
- No file rotation; no fsync per write — matches existing transcript convention.
