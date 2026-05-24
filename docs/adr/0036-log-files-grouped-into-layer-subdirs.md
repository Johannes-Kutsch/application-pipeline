# Log files grouped into layer subdirectories

Per-component log files move from flat `logs/<layer>_<comp>.events.jsonl` to `logs/<layer>/<comp>.events.jsonl`. Three layers: `parser/`, `llm/`, `pipeline/`. Shared aggregates (`lifecycle.jsonl`, `run.log`) stay at `logs/` root.

Component identifiers stay layer-prefixed in all data: `lifecycle.jsonl`, `run.log` headers, Status Display labels. Only the filename changes.

## Why

- Flat directory got noisy — dozen-plus entries with no visible group structure. The directory level is now cheaper than the noise.
- Prefix-in-identifier argument from ADR-0012 is unaffected — identifiers travel into row data where the directory isn't visible.

## Consequences

- `RunLog` derives layer from component-id prefix, strips prefix for filename.
- No migration — old flat files left alongside new tree.
- Amends ADR-0012: layout switches from flat-with-prefix to layer-subdirs-with-bare-filenames.
