# Freshness Gate drops stale listings

Drops candidates when `posted_date` exceeds `MAX_LISTING_AGE_DAYS` (default 180) or `deadline < anchored_today`. `None` on a field = no signal, don't drop on that field alone; both `None` → pass. `anchored_today` is the cron-anchored logical date (ADR-0015).

Exposes `admit(stub, *, gate_arm, deadline=None) -> bool` called at **three** sites (ADR-0042): post-discover and post-enrich on the parser thread, plus a standalone post-LLM arm inside the **LLM Enricher**. Drops from parser-thread arms summed into one `freshness` counter on the **Status Display** (ADR-0043).

Amended by ADR-0032 (two arms became three via ADR-0038).

## Why

- Freshness is distinct from domain fit — `out_of_domain` means "wrong professional fit, forever."
- Post-enrich is the only correct placement for parser-sourced dates; post-LLM catches LLM-inferred dates.
- Cron-anchored "today" keeps threshold stable across quota sleeps.

## Consequences

- `expired` dedup status. On `matched → expired`, the `{header, summary}` extract is deleted.
- `MAX_LISTING_AGE_DAYS: int` on `Config` (default 180, `≥ 1`).
- Future-dated `posted_date` (negative age) passes silently.
