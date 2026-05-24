# Log convention: split by reader, components are call sites, layer-prefixed

`<settings-dir>/.runtime-data/logs/` (ADR-0037) laid out by reader, grouped into layer subdirs (ADR-0036). Four file types:

- `<layer>/<comp>.events.jsonl` — one structured row per step. Run-end metrics land as `event=run_complete`.
- `lifecycle.jsonl` — shared file at `logs/` root carrying status-display register/phase_changed/removed events.
- `run.log` — shared file for tracebacks and `SUMMARY OF SESSION` blocks with `=== <component>  <ts>  <kind> ===` headers.
- `<layer>/<comp>.transcripts.jsonl` — LLM components only, full prompt/response payloads.

**Component = call site, not class.** `llm_classify_relevance` and `llm_judge_match` each own their files. Every identifier carries a layer prefix (`parser_`, `llm_`, `pipeline_`) in all data — lifecycle rows, `run.log` headers, Status Display labels. Subdir replaces prefix only on the filename.

## Why

- Per-step events analyzable via `jq`. Eyeball-mode artifacts in one shared `run.log`.
- "What was classify doing vs judge" is the dominant question — filing by call site makes the filter the filename.
- Layer prefix in the identifier avoids a mapping table that drifts.

## Consequences

- `ClaudeExtractor._invoke` routes writers to `site.component_id`. No `claude_extractor.*` stream.
- No file rotation; no fsync per write.
