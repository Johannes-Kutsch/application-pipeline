# Log convention: layer-prefixed components in layer subdirectories

`<settings-dir>/.runtime-data/logs/` (ADR-0027) grouped into layer subdirs: `parser/`, `llm/`, `pipeline/`. Pipeline-owned file types:

- `<layer>/<comp>.events.jsonl` — structured row per step. `event=run_complete` for run-end metrics.
- `lifecycle.jsonl` — shared file at `logs/` root for status-display events.
- `run.log` — shared file for tracebacks and `SUMMARY OF SESSION` blocks.

Production **LLM Extractor** evidence uses **Agent Runtime Logs** under `llm/agent-runtime/classify/` and `llm/agent-runtime/judge/` (ADR-0038). Pipeline-owned LLM transcript JSONL retired.

**Component = call site.** `llm_classify_relevance` and `llm_judge_match` each own their files. Every identifier carries a layer prefix (`parser_`, `llm_`, `pipeline_`) in all data; subdir replaces prefix only on filename.

## Why

- Per-step events analyzable via `jq`. Filing by call site makes the filter the filename.
- Layer prefix in identifier avoids a mapping table that drifts. Layer subdirs restore structure without affecting identifiers.

## Consequences

- No file rotation; no fsync per write. No migration — old flat files left alongside new tree.
