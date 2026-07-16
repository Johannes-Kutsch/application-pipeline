# Template tree splits into routing buckets; agent skills seeded by `init`

`src/application_pipeline/templates/` organised into per-destination buckets: `templates/application-pipeline/...` → `<cwd>/application-pipeline/`; `templates/claude/skills/...` → `<cwd>/.claude/skills/`.

Agent skills (`analyse-listing/`, `write-cv/`) are package-shipped, refreshable. `init --refresh` overwrites package-owned skill dirs; user-added content survives. `build-cv/` was merged into `write-cv/` (ADR-0041) and removed from the template tree.

Ride-along: `cv_skeleton.tex` moves from `<settings-dir>/skills/` to `<settings-dir>/cv-template/`.

## Why

- Skills evolve with the package. Bucket layout is self-documenting. Single source of truth.

## Consequences

- Refresh overwrites package-owned skill dirs. `init --refresh` deletes obsolete `skills/` dir.
