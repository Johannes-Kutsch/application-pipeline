# Deduplication state (`.seen.json`) is durable via Syncthing

> **Amended by [ADR-0022](0022-output-paths-anchored-to-data-dir.md):** `.seen.json` lives at `data/.seen.json` — inside the synced folder, **sibling to** (not inside) `data/results/`. The durability-via-Syncthing rationale below is unchanged; only the literal "alongside the Results File" placement is clarified to mean "in the same synced folder," so dedup state survives a `mv data/results data/results.archive` reset gesture.

The **Deduplication** store (`.seen.json`, holding seen URLs with `(company_lc, title_lc, location_lc, status, first_seen)`) lives in the Syncthing-synced `results/` folder alongside the **Results File**. The Pi is the single writer; the laptop holds a continuously-mirrored copy that serves as backup. The file is *not* tracked in git (it's listed in `.gitignore`).

## Why

- **It's the pipeline's memory across resets.** The **Results File** is rolling and gets reset manually when the applicant grabs it. If `.seen.json` resets too, every fresh **Results File** is flooded with positions the pipeline already showed yesterday — exactly the friction the project is supposed to remove.
- **Backup without a credential on the Pi.** The Pi runs unattended and also hosts unrelated agentic-coding work; we want no write credentials on it. Syncthing was already the transport for `current.md` — adding `.seen.json` to the same folder is free and the laptop's mirror is the disaster-recovery backup.
- **History is in the file, not the transport.** "When did I first see this URL?" is answered by the `first_seen` field inside each record. Git's per-commit history was overkill for that question — and the file's row-shape makes it grep-able directly.
- **Single-writer, no conflict surface.** Only the Pi writes `.seen.json`. The laptop never runs the full pipeline (no Ollama). Syncthing's send-receive folder mode handles the propagation; the single-writer invariant means no merge concerns ever arise on this file.

## Considered alternatives

- **Track `.seen.json` in git, Pi commits + pushes after each run** — held by an earlier revision of this ADR, rejected: requires a write credential on the Pi for a property (cross-machine sync) that doesn't exist in this deployment, since the Pi is the only writer. Backup is the actual requirement, not sync, and Syncthing already gives us backup.
- **Gitignore `.seen.json` with no backup at all** — rejected: a Pi disk failure or fresh checkout would erase the pipeline's memory and reflood the next **Results File** with old listings.
- **Periodic `rsync` snapshot to the laptop** — rejected: equivalent durability to Syncthing but requires the laptop be reachable from the Pi at snapshot time. Syncthing is eventually-consistent and tolerates either side being offline.

## Consequences

- **`.seen.json` is in `.gitignore`.** It is not a checked-in artifact.
- **Single-writer enforcement is load-bearing.** The crontab wraps the entry point with `flock -n /var/lock/application-pipeline.lock` so a still-running invocation causes the next cron tick to exit immediately rather than starting a second writer. This matters because run #1, or runs that hit Ollama retries, can plausibly exceed the cron interval.
- **Disaster recovery from laptop:** if the Pi's `.seen.json` is lost (disk failure, fresh reflash), copy the laptop's Syncthing copy back into place and resume. Syncthing's conflict-file handling absorbs any race window.
- **Schema migrations** of `.seen.json` are a deployment concern of the release tag — a tag that changes the on-disk shape must run an in-process migration on load.
- **If a future stage ever introduces a second writer**, this ADR's single-writer assumption breaks and the file would need a locking or merge strategy. Flag at that time.
