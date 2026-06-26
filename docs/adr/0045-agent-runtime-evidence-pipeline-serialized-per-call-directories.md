# Agent Runtime evidence: one log file per invocation

Pipeline owns evidence serialization for each **Agent Runtime** call (`ruhken-agent-runtime==0.0.5`). One plain-text `.log` file is written per invocation under `llm/agent-runtime/classify/` or `llm/agent-runtime/judge/` with a timestamp-based filename.

The file contains three sections in order:

1. **`[prompt]`** — the sent prompt, written to disk before `run_ephemeral` is called so evidence exists even if the call never returns.
2. **`[events]`** — one line per `AgentEvent` (`type | display_message`, with `raw_provider_output` appended when non-empty), streamed live via `EphemeralRunRequest.on_live_output` as each event arrives.
3. **`[result]`** — outcome kind, selected provider (`service`, `model`, `effort`), and `usage` if present, written once after `run_ephemeral` returns.

`AgentRuntimeInvocationResult.evidence_path` carries the file path and is consumed by **Malformed Classify Stash** and **Failure Report** as an opaque pointer.

## Why

`ruhken-agent-runtime==0.0.5` removes `RuntimeOutcome.invocation_records` and `InvocationRecord` entirely. The previous batch-write model (per-call directory with separate `prompt`, `response`, `events`, `meta` files) cannot be reconstructed from the new API. The replacement uses `on_live_output` to stream events as they arrive, which also improves the diagnostic value of partial logs from interrupted calls.

## Consequences

- **Maintenance** deletes the `.log` file (not a directory) when older than 30 days.
- Serialization errors at any write point are swallowed — cannot break **Daily Results File** production.
- Missing evidence is a diagnostic gap, not a failure.
- ADR-0042 body references `ruhken-agent-runtime==0.0.2`; that ADR records the past decision to adopt Agent Runtime and need not be updated — the live pin is in `pyproject.toml`.
