# Quota handling: parse reset time and sleep through it

On provider usage-limit outcomes, sleep until `reset_time + 2 minutes`, then retry. Missing reset time falls back to `next_top_of_hour + 2min`. No retry budget cap.

## Why

- Under once-per-day model, silently skipping is worse than delaying. 2-minute buffer operationally validated. No cap: `flock` surfaces extreme outages.

## Consequences

- **Agent Runtime** usage-limit outcomes carry `reset_time`. Sleep at orchestrator level for judge; through **Quota Wall** (ADR-0024) for classify workers.
- Runs either complete or fail. No `degraded_reason`.
