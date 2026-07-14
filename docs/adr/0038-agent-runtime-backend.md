# Agent Runtime backend; evidence serialization; no usage telemetry

Production **LLM Extractor** backend: `ruhken-agent-runtime==0.1.2`. Service `opencode`, model `deepseek-v4-flash`, `ToolAccess.no_tools()`. Service/model/tool policy pinned inside the extractor — not operator config. `CLAUDE_CLI_PATH` retired. `HardAgentError` propagates as a fatal exception (not caught and converted). Prompt normalization: Unicode `"Zs"` → ASCII space; `"Cf"` characters dropped.

**Evidence**: Pipeline owns serialization — one plain-text `.log` per invocation under `llm/agent-runtime/classify/` or `llm/agent-runtime/judge/`. Three sections: `[prompt]` (written before call), `[events]` (streamed live via `on_live_output`), `[result]` (outcome + provider + optional usage). `AgentRuntimeInvocationResult.evidence_path` is an opaque pointer consumed by **Malformed Classify Stash** and **Failure Report**. Maintenance deletes `.log` files older than 30 days.

**Usage telemetry**: **LLM Extractor** treats usage/token counts/cost as non-contract. Missing usage does not make valid output malformed. Pipeline discards ordinary runtime usage — no token totals, cost, or duration in contract, **Run Summary**, or CLI output. `usage_limit` remains a control-flow outcome for **Quota Wall** (ADR-0023) and judge retry (ADR-0011). Raw per-call `usage` appears only in evidence logs.

## Why

- Replaces direct `claude -p` subprocess calls with Agent Runtime, preserving classifier/judge semantics while changing the provider boundary.
- `ruhken-agent-runtime==0.0.5` removed `invocation_records`; `on_live_output` streams events live with better partial-log diagnostics for interrupted calls.
- Prefers reliable **Daily Results File** production over incomplete provider telemetry.

## Consequences

- Pipeline-owned LLM transcript JSONL retired; evidence is **Agent Runtime Logs** only.
- Serialization errors are swallowed — cannot break **Daily Results File** production.
- Missing evidence is a diagnostic gap, not a failure.
- Consistent with ADR-0040: fatal errors (including `HardAgentError`) surface as unhandled exceptions to stderr.
