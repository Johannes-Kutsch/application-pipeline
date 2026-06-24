# Agent Runtime opencode as the LLM backend

Production **LLM Extractor** backend is `ruhken-agent-runtime==0.0.2` ephemeral calls. Service `opencode`, model `deepseek-v4-flash`, `ToolAccess.no_tools()`. Project-facing contract, batching, tag-wrapped output (ADR-0006), quota wall (ADR-0024), stateless per-call prompt shape all preserved.

Service, model, and tool policy pinned behind the extractor — not operator config. `CLAUDE_CLI_PATH` retired; rejected at config load.

Current code uses backend-neutral or Agent Runtime names. Historical ADRs may keep `Claude*` names for past decisions, but live config/exceptions/metrics must not expose `Claude*` vocabulary.

## Why

- Replaces direct `claude -p` subprocess calls with Agent Runtime, preserving classifier/judge semantics while changing the provider boundary.

## Consequences

- Production prompt/response evidence is **Agent Runtime Logs** only; pipeline-owned LLM transcript JSONL retired.
- `RelevanceVerdict` = `{matches, header?, summary?}`. `MatchVerdict` = `{id: int, rank}`.
