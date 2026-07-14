# Daily results file replaces the trio

One dated markdown file per day at `<settings-dir>/results/YYYY-MM-DD.md`, holding **Daily Top-5** in **Rank** order. No preamble, no Run Divider.

## Why

- Smallest artifact answering "what are today's best 5 matches?" Daily files self-reset by date; write-once simplifies Syncthing semantics.

## Consequences

- Surface: `ensure_initialized(path)` (mkdir) and `append(path, rendered_block)` (write + flush + fsync).
- **Cron-anchored logical day**: `date_for_file = run_started_at.date()`.
- `<5` candidates → file carries however many exist. `=0` → no file.
