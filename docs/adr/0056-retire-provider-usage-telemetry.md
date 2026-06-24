# Retire provider usage telemetry

The **LLM Extractor** no longer treats provider usage, token counts, cost, or duration as part of its project-facing contract. Classifier and **Match Judge** calls are valid when their output satisfies the **Agent Output Protocol**; missing usage metadata from **Agent Runtime** must not turn otherwise valid output into malformed output. The pipeline discards ordinary runtime usage even when Agent Runtime provides it: no `CallUsage`, token totals, cost totals, duration totals, or provider-usage fields appear in the LLM Extractor contract, **Run Summary**, Run Divider, or CLI `run complete` output. This prefers reliable **Daily Results File** production over incomplete provider telemetry.

`usage_limit` remains a control-flow outcome, not usage telemetry. The **Quota Wall** and judge retry behavior still use Agent Runtime usage-limit reset-time events/errors per ADR-0016.

Scope (clarified per #972): this retirement governs the **LLM Extractor** contract and pipeline-owned summary/aggregate surfaces (Run Summary, Run Divider, CLI `run complete`, counters). It does not govern the raw per-call **Agent Runtime Logs** evidence, where `usage` may be serialized verbatim as part of the returned `InvocationRecord` dump (ADR-0057). Usage in that evidence is per-call and never summed; it must not flow back into the contract or any summary surface.
