# Deduplication state durable via Syncthing

`seen.json` at `<settings-dir>/.runtime-data/seen.json` (ADR-0028), inside the Syncthing-synced folder. Pi sole writer; laptop mirror is backup.

## Why

- Pipeline memory across resets. Backup without a Pi credential. Single-writer, no conflict surface.

## Consequences

- `.gitignore`d. No auto-migration on schema changes — deployment wipes instead.
