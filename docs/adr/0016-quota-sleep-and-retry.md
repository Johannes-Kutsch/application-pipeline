# Quota handling: parse reset time and sleep through it

On provider usage-limit outcomes, the pipeline uses the **Agent Runtime** reset-time event/error when available, sleeps until `reset_time + 2 minutes`, then retries. Missing reset time falls back to `next_top_of_hour + 2min`. No retry budget cap. Companions: ADR-0014 (top-N), ADR-0015 (daily file), ADR-0017 (once-per-day).

## Why

- Degrading to no-ops loses data on the day the user most wants output. Under once-per-day model, silently skipping is worse than delaying.
- The provider/runtime boundary can carry the reset time directly; the pipeline's concern is sleep-and-retry, not provider-specific parsing.
- 2-minute buffer is operationally validated — Anthropic's counters take a beat to open.
- No cap: cron-overlap via `flock` surfaces extreme outages; applicant prefers "delayed file" over "no file."

## Consequences

- **Agent Runtime** usage-limit outcomes carry `reset_time` when known. Sleep at orchestrator level for **Match Judge** and through the **Quota Wall** for classify workers.
- Classify retry: worker publishes to the **Quota Wall** (ADR-0031), sleeps, retries. Judge retry: re-issues same candidates.
- `degraded_reason` field removed entirely. Runs either complete or fail.
