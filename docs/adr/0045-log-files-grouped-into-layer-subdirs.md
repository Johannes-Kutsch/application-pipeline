# Log files grouped into layer subdirectories

Per-component log files move from a flat `logs/<layer>_<comp>.events.jsonl` layout to `logs/<layer>/<comp>.events.jsonl` (and the same for `.transcripts.jsonl`). The three layers — `parser/`, `llm/`, `pipeline/` — each get a subdirectory; component filenames drop their layer prefix on disk. The shared aggregates `lifecycle.jsonl` and `run.log` stay at `logs/` root. Component identifiers themselves stay layer-prefixed everywhere they travel as data: `lifecycle.jsonl` `component` fields, `run.log` `=== <component>  <ts>  <kind> ===` headers, and Status Display row labels all keep `parser_bundesagentur_api`, `llm_classify_relevance`, `pipeline_orchestrator` etc. unchanged. Only the filename changes.

## Why

- **The flat directory got noisy.** ADR-0018 rejected subdirs on the assumption that sorted-flat would scale. With three parsers, two LLM call sites, four pipeline stages, plus their `.transcripts.jsonl` siblings and the two shared aggregates, `ls logs/` runs to a dozen-plus entries with no visible group structure. The argument has flipped: the directory level is now cheaper than the noise it removes.
- **The "no readability gain" claim no longer holds.** When there were three or four files, scanning by prefix was easy. With the current count it isn't, and every new parser or call site makes it worse.
- **The prefix-in-identifier argument is unaffected.** ADR-0018's reason for putting the layer in the identifier — "avoids a parallel mapping table that drifts" — applies to the *identifier*, not the filename. Once you read a row out of `lifecycle.jsonl` or look at a `run.log` header, the directory structure isn't visible; the prefix on the identifier is what conveys the layer there. So the subdir is added *in addition to* the prefix, not instead of it.

## Considered alternatives

- **Drop the layer prefix from identifiers too** (subdir replaces the prefix everywhere). Rejected: identifiers travel into row data (`component` field in `lifecycle.jsonl`, headers in `run.log`, status-display labels) where there is no directory to convey the layer. The prefix earns its keep there even if the filename no longer needs it. Reverses ADR-0018's "prefix is part of the identifier" rule for no readability gain at the rows.
- **Auto-migrate existing flat files on first run.** Rejected for the same reason ADR-0018 rejected its own auto-rename: edge-case-y for a one-time cutover; operator deletes legacy files by hand or leaves them next to the new tree.
- **Keep the flat layout, add a generated `INDEX.md`.** Rejected: adds a refresh-or-stale concern for a problem that subdirs solve directly.

## Consequences

- **`RunLog` (parser_log)** writes per-component event/transcript files under `logs/<layer>/`. The layer is derived from the existing component-id prefix (`parser_*` → `parser/`, `llm_*` → `llm/`, `pipeline_*` → `pipeline/`); the filename uses the identifier with its prefix stripped. Shared writes (`lifecycle.jsonl`, `run.log`) keep their current path at `logs/` root.
- **No file rotation, no fsync per write** — unchanged from ADR-0018.
- **No migration.** Existing flat files in `logs/` are left in place; new runs write to the subdir tree alongside them. The operator deletes the old files by hand when convenient.
- **CONTEXT.md "Log Artifacts" entry** updates the path examples and adds the rule that identifiers stay prefixed in row content.
- **Tooling (`jq` recipes, status display, anything that opens log files by name)** updates path construction. The identifier-prefix rule in ADR-0018 §"Every component identifier carries a layer prefix" still holds — only the filename derivation rule changes.
- **Status Display labels are unchanged** — they already render the layer-prefixed identifier and that surface is not on disk.

## Supersedes / amends

- **Amends ADR-0018.** Layout switches from flat-with-filename-prefix to layer-subdirs-with-bare-filenames. The identifier rule (prefix is part of the identifier, appears in `lifecycle.jsonl` / `run.log` headers / Status Display) is preserved. ADR-0018's rejection of "subdirectories instead of prefixes" is the line being flipped — the reasoning then was "no readability gain"; the count of files has since made that no longer true.
