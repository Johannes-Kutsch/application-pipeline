# Deduplication state (`.seen.json`) is committed to git

The **Deduplication** store (`.seen.json`, holding seen URLs and `(company, title, city)` tuples with `first_seen` dates) is checked into the repository instead of being gitignored.

## Why

- **It's the pipeline's memory across resets.** The **Results File** is rolling and gets reset manually when the applicant grabs it; if `.seen.json` resets too, every fresh **Results File** is flooded with positions the pipeline already showed yesterday — exactly the friction the project is supposed to remove.
- **It serves as the Pi↔laptop sync channel for dedup state in v1.1.** **Syncthing** is reserved for the **Results File** (which behaves like ephemeral filesystem state). Dedup state benefits from git's history and conflict semantics: small, mostly append-only, low churn.
- **Single-developer repo.** No multi-author conflict surface. The data is not secret.

## Considered alternatives

- **Gitignore `.seen.json`** (the original PRD #3 approach) — rejected: would leave Pi and laptop with divergent dedup memory after deployment, and the laptop would re-flood the file every time the Pi laptop relationship changes.
- **Sync `.seen.json` via Syncthing alongside the Results File** — rejected: bidirectional sync of a state file with simultaneous writers (Pi cron + laptop run) risks silent overwrites; git's serialization is preferable for this artifact.
