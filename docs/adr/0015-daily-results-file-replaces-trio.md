# Daily results file replaces the trio; FILE_HEADER and Run Divider retired

One dated markdown file per calendar day at `<settings-dir>/results/YYYY-MM-DD.md`, holding the **Daily Top-5** in **Rank** order. No preamble, no Run Divider (metrics move to `pipeline_orchestrator.events.jsonl` per ADR-0012). Companions: ADR-0014 (tier retired), ADR-0016 (quota), ADR-0017 (one run per day).

## Why

- A dated file per day is the smallest artifact answering "what are today's best 5 matches?"
- Daily files self-reset by date; Run Divider was a multi-fire-per-day heartbeat that no longer applies under once-per-day firing.
- Syncthing semantics simplify — write-once, never re-touched.

## Consequences

- **Results File Manager** surface: `ensure_initialized(path)` (just `mkdir`) and `append(path, rendered_block)` (write + flush + fsync).
- **Cron-anchored logical day**: `date_for_file = run_started_at.date()`. Same path regardless of sleep/midnight crossing.
- `<5` candidates → file carries however many exist. `=0` → no file written.
