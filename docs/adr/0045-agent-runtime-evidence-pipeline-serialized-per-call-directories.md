# Agent Runtime evidence: pipeline-serialized per-call directories

Pipeline owns serialization of `InvocationRecord` evidence from `ruhken-agent-runtime==0.0.2`. The runtime no longer writes log files; `RuntimeOutcome.invocation_records` returns consumer-owned evidence. The runtime's `invocation_dir` is ephemeral scratch only.

Each classifier or **Match Judge** call produces one curated evidence *directory* under `llm/agent-runtime/classify/` and `llm/agent-runtime/judge/`. Directory holds separate files:

- `prompt` — the sent prompt (large, carries full **Raw Description** bodies).
- `response` — decoded provider output.
- `events` — `AgentEvent` stream (most useful diagnostic view).
- `meta` — thin: provider session id, outcome, usage.

Multiple `InvocationRecord`s from one call stay in one directory with index suffixes. `usage` in `meta` is raw per-call evidence only — does not reverse ADR-0044.

## Why

- Faithful dump of runtime evidence rather than re-deriving a pipeline schema. Separate files so each concern is independently readable.

## Consequences

- **Maintenance** deletes whole directory when older than 30 days. Missing/empty evidence is a diagnostic gap, not a failure.
- Serialization errors are best-effort — cannot break **Daily Results File** production.
- **Malformed Classify Stash** and **Failure Report** point at per-call evidence directory.
