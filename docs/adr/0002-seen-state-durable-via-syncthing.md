# Deduplication state (`.seen.json`) is durable via Syncthing

`.seen.json` lives at `data/.seen.json` (sibling to `data/results/`, per ADR-0011), inside the Syncthing-synced folder. The Pi is sole writer; the laptop's mirror is the backup. Not tracked in git.

## Why

- **Pipeline memory across resets.** The **Daily Results File** is per-day; if `.seen.json` reset too, every fresh day floods with listings the pipeline already showed.
- **Backup without a credential on the Pi.** Syncthing was already the transport for results — adding `.seen.json` is free, and the laptop mirror is disaster recovery.
- **History in the file, not the transport.** `first_seen` answers "when did I first see this URL"; git per-commit history was overkill.
- **Single-writer, no conflict surface.** Only Pi writes. Laptop never runs the full pipeline. Sibling-to-results placement also survives a `mv data/results data/results.archive` reset gesture.

## Consequences

- `.gitignore`d.
- Crontab wraps the entry point with `flock -n` so a still-running invocation causes the next cron tick to exit immediately.
- Disaster recovery: copy laptop's Syncthing copy back into place.
- Schema migrations: see ADR-0024 — the v2 cutover wipes the file instead.
- If a future stage introduces a second writer, the single-writer assumption needs a locking/merge strategy.
