# Template tree splits into routing buckets; `.claude/skills/` seeded by `init`

Amended by ADR-0048: Agent Skill workflow bodies now live in `application-pipeline/agent-skills/`, with Claude and Codex wrappers seeded under `.claude/skills/` and `.codex/skills/`.

`src/application_pipeline/templates/` reorganises into per-destination buckets:

- `templates/application-pipeline/...` → seeds to `<cwd>/application-pipeline/`.
- `templates/claude/skills/...` → seeds to `<cwd>/.claude/skills/`.

Agent skills (`analyse-listing/`, `write-cv/`) become package-shipped, refreshable artefacts. Shared helper docs are folded into those skills where they are only used by one workflow. Source of truth moves from repo-root `.claude/skills/` into `templates/claude/skills/`.

Ride-along: `cv_skeleton.tex` moves from `<settings-dir>/skills/` to `<settings-dir>/cv-template/`, eliminating the conceptual collision with `.claude/skills/`.

## Why

- Skills encode pipeline-specific workflows that evolve with the package. Treating them as hand-maintained guarantees drift.
- Bucket layout is self-documenting — first path segment declares the destination.
- Single source of truth eliminates duplicate-file drift.
- `init --refresh` contract ("overwrite known files, never delete") extends naturally.

## Consequences

- `init_cmd.py` seeds per-bucket. Repo-root `.claude/skills/` deleted from version control.
- Refresh overwrites package-owned skill dirs; user-added skill dirs and operator notes inside package dirs survive.
- `cv_skeleton.tex` host path: `<cwd>/application-pipeline/cv-template/cv_skeleton.tex`. `init --refresh` deletes obsolete `skills/` dir.
- Amends ADR-0008 (init semantics), ADR-0022 (two roots), ADR-0023 (cv_skeleton path).
