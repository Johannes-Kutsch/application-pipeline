# Pipeline-written paths grouped under `.runtime-data/`

All pipeline-written paths move into `<settings-dir>/.runtime-data/`: `seen.json` (renamed from `.seen.json`), `extracts.json`, `logs/`, `failures/`. `results/` and `applications/` stay at root — user-consumed, not run-internal.

`.seen.json` loses its leading dot (redundant inside a dot-prefixed parent). `.gitignore` seeded by `init` ignores `.runtime-data/`.

## Why

- Settings-dir root now contains only what the operator authored, package shipped, or operator consumes. Pipeline-written state is one dir away.
- Reset-survival (ADR-0008) preserved — `.runtime-data/` is sibling to `results/`, not nested inside it.
- No new sync policy — parent is the synced unit.

## Consequences

- `Config`/`DataPaths` rebase derived paths under `.runtime-data/`. `results_dir` stays at root.
- Lazy `mkdir` — created on first write.
- `init --refresh` does not touch `.runtime-data/`.
- Manual migration: `mv .seen.json .runtime-data/seen.json` etc. If forgotten, fresh `seen.json` starts on next tick.
- Amends ADR-0002, ADR-0007, ADR-0008, ADR-0012/ADR-0036.
