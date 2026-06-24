# Retire provider usage telemetry

**LLM Extractor** no longer treats provider usage/token counts/cost/duration as contract. Valid output = satisfies **Agent Output Protocol**; missing usage must not make valid output malformed. Pipeline discards ordinary runtime usage: no `CallUsage`, token totals, cost totals in extractor contract, **Run Summary**, Run Divider, or CLI output.

`usage_limit` remains a control-flow outcome for **Quota Wall** and judge retry (ADR-0012).

Raw per-call `usage` may appear in **Agent Runtime Logs** evidence (ADR-0045) — per-call only, never summed, never flows back into contract or summaries.

## Why

- Prefers reliable **Daily Results File** production over incomplete provider telemetry.
