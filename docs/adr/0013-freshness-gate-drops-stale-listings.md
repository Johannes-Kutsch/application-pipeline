# Freshness Gate drops stale listings

Drops when `posted_date` exceeds `MAX_LISTING_AGE_DAYS` (default 180) or `deadline < anchored_today`. `None` = no signal, don't drop. `anchored_today` = cron-anchored logical date (ADR-0011).

`admit(stub, *, gate_arm, deadline=None) -> bool` at three sites (ADR-0033): post-discover, post-enrich (parser thread), post-LLM (**LLM Enricher**). Parser-thread drops summed into one `freshness` counter (ADR-0034).

## Why

- Freshness distinct from domain fit. Post-enrich is correct for parser-sourced dates; post-LLM catches LLM-inferred dates.

## Consequences

- `expired` dedup status. On `matched → expired`, extract deleted.
- `MAX_LISTING_AGE_DAYS: int` on `Config` (default 180, `≥ 1`).
