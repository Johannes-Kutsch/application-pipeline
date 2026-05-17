# Log convention: split streams by reader, not by component

The `data/logs/` directory is laid out by *who reads what*, not by which component emitted it. Three streams coexist:

- `<comp>.events.jsonl` — one structured row per step emitted by component `<comp>` (`{ts, event, ...fields}`, free-form keys). Read by `jq` and analysis scripts. One file per component.
- `lifecycle.jsonl` — a single shared file carrying status-display register / phase_changed / removed events from every component, for post-mortem timeline reconstruction of a run that hung or skipped a phase.
- `run.log` — a single shared file carrying tracebacks and end-of-run `SUMMARY OF SESSION` blocks from every component, chronologically interleaved with `=== <component>  <timestamp>  <kind> ===` headers. Read by human eyeballs after a run.
- `<comp>.transcripts.jsonl` (LLM components only) — full prompt/response payloads. Kept distinct from `<comp>.events.jsonl` because the payloads are large strings with a different reader (prompt debugging vs step tracing).

## Why

The previous convention was "one `.log` per component, mixing every kind of line". In practice that produced seven-line ghost files for components whose only on-disk emissions were status-display chatter (`prefilter.log`, `dedup.log`, `pipeline.log`, `judge_match.log`, `startup.log`, `classify_relevance.log`), while real diagnostic events were unstructured text mixed with tracebacks and lifecycle noise (`bundesagentur_api.log` at 1622 lines).

Splitting by reader instead of by component:

- **Empties out the chatter.** Lifecycle events from every component funnel into one file (`lifecycle.jsonl`), so components with no other on-disk output stop producing ghost files.
- **Makes per-step events analyzable.** `<comp>.events.jsonl` is the place a `jq` pipeline can answer "how many `query_started` did Bundesagentur emit at each location" without grepping unstructured text.
- **Keeps eyeball-mode artifacts in one place.** Tracebacks and end-of-run summaries are the things an operator actually opens in a text editor after a run; collapsing them into `run.log` means one tail to read, with `===` headers preserving component attribution.

## Considered alternatives

- **Single global `events.jsonl` firehose.** Rejected: greppability by component is the dominant use case (tail-and-jq one source without filtering noise from others), and per-component files keep that natural.
- **Per-component `.log` files retained, with chatter dropped in place.** Rejected: even with chatter removed, components like `prefilter` and `dedup` would still produce sparse files containing only an end-of-run summary block, multiplying the artifact set for no readability gain.
- **Fold `<comp>.transcripts.jsonl` into `<comp>.events.jsonl`.** Rejected: LLM prompt/response payloads are large strings that bloat the events stream, and the readership is different (prompt debugging vs step tracing).
- **Include a `component` field in each `.events.jsonl` row.** Rejected: redundant with the filename, and the row is the natural unit of attribution only when in context — anything moving a row outside its file should re-attribute on the way out.

## Consequences

- `parser_log` gains a structured-event writer that emits to `<comp>.events.jsonl` and a lifecycle writer that emits to the shared `lifecycle.jsonl`. The text `record()` function is repurposed (or replaced) so that what used to be `<ts> <event> k=v` text lines now become JSONL rows.
- The status-display module stops calling the per-component text logger on register / phase_changed / removed and routes those events to `lifecycle.jsonl` instead.
- `parser_log.summarize` writes its block into the shared `run.log` with a `=== <component>  <ts>  summary ===` header instead of `<comp>.log`. `record_traceback` does the same with `kind=traceback`.
- The CONTEXT.md **Status Display** entry's claim that lifecycle events land in the row's namesake component log is no longer accurate — they land in `lifecycle.jsonl`.
- No file rotation, no fsync per write — matches the existing transcript-writing convention.
