# Agent Runtime opencode as the LLM backend

Production **LLM Extractor** backend is `ruhken-agent-runtime==0.1.2` ephemeral calls. Service `opencode`, model `deepseek-v4-flash`, `ToolAccess.no_tools()`. Project-facing contract, batching, tag-wrapped output (ADR-0006), quota wall (ADR-0024), stateless per-call prompt shape all preserved.

Service, model, and tool policy pinned behind the extractor — not operator config. `CLAUDE_CLI_PATH` retired; rejected at config load.

Current code uses backend-neutral or Agent Runtime names. Historical ADRs may keep `Claude*` names for past decisions, but live config/exceptions/metrics must not expose `Claude*` vocabulary.

## Why

- Replaces direct `claude -p` subprocess calls with Agent Runtime, preserving classifier/judge semantics while changing the provider boundary.

## Consequences

- Production prompt/response evidence is **Agent Runtime Logs** only; pipeline-owned LLM transcript JSONL retired.
- `RelevanceVerdict` = `{matches, header?, summary?}`. `MatchVerdict` = `{id: int, rank}`.

## 0.1.2 upgrade

`AgentRuntimeError` removed from the package's public API in 0.1.2. Two consequences for **Agent Runtime Invocation**:

**Error surfacing.** `HardAgentError` now propagates as a fatal exception through `invoke_agent_runtime` — it is no longer caught and converted to a `hard_provider_failure` result. Consistent with ADR-0046: fatal errors surface as unhandled exceptions to stderr. Outcome-based hard failures (`ProviderUnavailable` non-transient, `Cancelled`, `TimedOut`) continue to map to `hard_provider_failure` and surface via `ExtractorUnreachableError` in the **LLM Extractor**.

**Prompt normalization.** `_normalize_prompt` extended to strip Unicode category `"Cf"` (format characters — zero-width spaces, directional markers) in addition to replacing `"Zs"` (space separators) with ASCII space. Root cause: `​` (U+200B ZERO WIDTH SPACE, category `"Cf"`) in scraped job bodies caused `UnicodeEncodeError` on Windows cp1252 stdin encoding at the AR process boundary, triggering a `TemporaryDirectory` cleanup failure cascade. `"Cf"` characters carry no semantic content in job description text and are dropped.
