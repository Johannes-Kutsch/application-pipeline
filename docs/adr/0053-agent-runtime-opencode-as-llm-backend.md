# Agent Runtime opencode as the LLM backend

The production **LLM Extractor** keeps its project-facing contract, batching, tag-wrapped output protocol, quota wall semantics, and stateless per-call prompt shape, but its backend moves from direct `claude -p` subprocess calls to pinned `ruhken-agent-runtime==0.0.1` ephemeral calls. Service, model, and tool policy are pinned behind the extractor as `opencode`, `deepseek-v4-flash`, and `ToolPolicy.NONE`; they are not operator config. This preserves the earlier decision that the pipeline owns classifier/judge semantics while replacing the provider execution boundary with Agent Runtime.

The installed `ruhken-agent-runtime==0.0.1` API is authoritative for this migration, not the moving GitHub `main` public API, because the two public surfaces currently differ.

Amendment (#972): the pin has since moved to `ruhken-agent-runtime==0.0.2`, whose returned-evidence logging API replaced the reserved-log-path model. See ADR-0057 for the resulting evidence-serialization decision; the installed `0.0.2` surface is now the authoritative one.

`CLAUDE_CLI_PATH` is retired and should fail config load like other removed config fields; silently ignoring it would hide a stale provider assumption.

Current production/backend code and tests should use backend-neutral or Agent Runtime names. Historical ADRs may keep `Claude*` names where they describe past decisions, but current behavior must not expose `Claude*` vocabulary through live config, exceptions, metrics, or extractor class names.
