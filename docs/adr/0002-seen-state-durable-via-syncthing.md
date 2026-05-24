# Deduplication state durable via Syncthing

`seen.json` lives at `<settings-dir>/.runtime-data/seen.json` (ADR-0037), inside the Syncthing-synced folder. Pi is sole writer; laptop mirror is the backup.

## Why

- Pipeline memory across resets — without it, every fresh day floods with already-shown listings.
- Backup without a credential on the Pi. Syncthing already transports results; adding `seen.json` is free.
- Single-writer, no conflict surface.

## Consequences

- `.gitignore`d. Crontab wraps with `flock` so overlapping ticks are serialised.
- Schema migrations: ADR-0017 wipes instead of migrating.
