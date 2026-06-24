# Pipeline-written paths grouped under `.runtime-data/`

All pipeline-written paths under `<settings-dir>/.runtime-data/`: `seen.json`, `extracts.json`, `logs/`, `failures/`. `results/` and `applications/` stay at root — user-consumed, not run-internal.

`.seen.json` loses leading dot (redundant inside dot-prefixed parent).

## Why

- Settings-dir root now contains only operator-authored, package-shipped, or operator-consumed items. Pipeline-written state is one dir away.

## Consequences

- Derived paths rebase under `.runtime-data/`. `results_dir` stays at root.
- Lazy `mkdir` on first write. `init --refresh` does not touch `.runtime-data/`.
