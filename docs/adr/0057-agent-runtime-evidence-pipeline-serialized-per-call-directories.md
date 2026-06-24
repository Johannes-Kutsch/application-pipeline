# Agent Runtime evidence is pipeline-serialized into per-call directories

Supersedes ADR-0054.

ADR-0054 assumed **Agent Runtime** writes provider-level `.log` files itself and the pipeline only chooses their location, ceding "provider-event shape" to the runtime. With `ruhken-agent-runtime == 0.0.2` that premise is gone: the runtime no longer writes a log artifact. `RuntimeOutcome` returns consumer-owned evidence as `invocation_records: tuple[InvocationRecord, ...]`, each record carrying the raw provider output, the event stream, usage, a provider session id, and an outcome. The runtime's `invocation_dir` survives only as an ephemeral worktree (prompt copy, `resume.jsonl`, session-id) — runtime scratch, not the diagnostic artifact.

So the pipeline now owns serialization. **Agent Runtime Logs** become a best-effort, pipeline-rendered serialization of the returned `InvocationRecord` evidence — not a file the runtime produces. The runtime owns the evidence *content*; the pipeline owns how that content is laid down on disk. We dump the runtime's evidence faithfully rather than re-deriving a pipeline schema, so it stays diagnostic and does not become a pipeline-owned transcript by the back door (pipeline-owned LLM transcript JSONL stays retired).

Each production classifier or **Match Judge** call produces one curated evidence *directory* per call (not a single `.log`), under the existing `llm/agent-runtime/classify/` and `llm/agent-runtime/judge/` subdirs. The directory holds separate files so each concern is independently readable instead of re-tangled into one transcript blob:

- `prompt` — the sent prompt (its own file precisely because it is the large one, carrying full **Raw Description** bodies);
- `response` — the decoded provider output;
- `events` — the `AgentEvent` stream, always included (it is the most useful diagnostic view);
- `meta` — thin: provider session id, outcome, and usage.

A single call can return multiple `InvocationRecord`s (retries/continuations); these stay in the one per-call directory with index suffixes when more than one record exists, preserving the "one directory per call" mental model and keeping pointers simple. The runtime's own `invocation_dir` worktree is kept separate from this curated directory so runtime scratch never pollutes the evidence or its retention.

`usage` is written into `meta` as raw per-call evidence only. This does not reverse ADR-0056: usage never re-enters the **LLM Extractor** contract, `RelevanceVerdict`/`MatchVerdict`, `AgentRuntimeResponse`, **Run Summary**, Run Divider, CLI `run complete`, or any pipeline counter, and missing usage never affects malformed/failure classification.

Because evidence is now a per-call directory rather than an append-forever file, **Maintenance** deletes the whole directory when older than 30 days instead of tail-truncating. Missing or empty evidence (directory absent or its files empty) is a diagnostic gap, not a classifier or judge failure, and serialization/write errors are best-effort so they cannot break **Daily Results File** production. The **Malformed Classify Stash** and **Failure Report** point at the per-call evidence directory where evidence exists.
