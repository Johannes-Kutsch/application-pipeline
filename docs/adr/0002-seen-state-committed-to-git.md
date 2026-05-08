# Deduplication state (`.seen.json`) is committed to git

The **Deduplication** store (`.seen.json`, holding seen URLs with `(company_lc, title_lc, location_lc, status, first_seen)`) is checked into the repository instead of being gitignored.

## Why

- **It's the pipeline's memory across resets.** The **Results File** is rolling and gets reset manually when the applicant grabs it; if `.seen.json` resets too, every fresh **Results File** is flooded with positions the pipeline already showed yesterday — exactly the friction the project is supposed to remove.
- **Backup and history.** The Pi is the only writer (the laptop never runs the full pipeline), so cross-machine sync is not the motivation. Git tracking gives the Pi's dedup memory a backup, a recoverable history, and a paper trail of when entries were first seen — useful for debugging false-positive dedup hits.
- **Single-developer, single-writer repo.** No multi-author or multi-machine write conflict surface. The data is not secret.

## Considered alternatives

- **Gitignore `.seen.json`** — rejected: a Pi disk failure or fresh checkout would erase the pipeline's memory and reflood the next **Results File** with old listings.
- **Store on the Pi only (no git tracking)** — rejected: same loss-on-disk-failure problem, no history, no easy way to inspect "when did I first see this URL?" from the laptop.

## Consequences

- The Pi must `git add` / `git commit` `.seen.json` after each run (or periodically) for the backup property to actually hold. The exact mechanism (post-run hook, cron-tail commit, etc.) is a v1.1 deployment concern.
- The single-writer property holds only if cron invocations cannot overlap. v1.1 deployment wraps the entry point with `flock -n /var/lock/application-pipeline.lock <command>` so a still-running invocation causes the next cron tick to exit immediately rather than starting a second writer. This matters because run #1, or runs that hit Ollama retries, can plausibly exceed the cron interval.
- If a future stage ever introduces a second writer, this ADR's single-writer assumption breaks and the file would need a locking or merge strategy. Flag at that time.
