# Quota handling: parse reset time and sleep through it

On Claude CLI 429, pipeline parses the human-readable reset time from `result` text, sleeps until `reset_time + 2 minutes`, retries. Unparseable → fallback `next_top_of_hour + 2min`. No retry budget cap. Companions: ADR-0014 (top-N), ADR-0015 (daily file), ADR-0017 (once-per-day).

## Why

- Degrading to no-ops loses data on the day the user most wants output. Under once-per-day model, silently skipping is worse than delaying.
- Anthropic's 429 already carries the reset time; the `pycastle/` plugin already parses this format.
- 2-minute buffer is operationally validated — Anthropic's counters take a beat to open.
- No cap: cron-overlap via `flock` surfaces extreme outages; applicant prefers "delayed file" over "no file."

## Consequences

- `parse_reset_time(result_text) -> datetime | None` in `llm/quota.py`, ported from `pycastle/`.
- `ClaudeUsageLimitError` carries `reset_time`. Sleep at orchestrator level, not inside `ClaudeExtractor`.
- Classify retry: worker publishes to the **Quota Wall** (ADR-0031), sleeps, retries. Judge retry: re-issues same candidates.
- `degraded_reason` field removed entirely. Runs either complete or fail.
