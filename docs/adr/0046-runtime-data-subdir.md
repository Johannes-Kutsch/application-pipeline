# Pipeline-written paths grouped under `.runtime-data/`

All paths the pipeline writes during a run move from the settings-dir root into a single `.runtime-data/` subdirectory:

```
<settings-dir>/
├── config.py              ← user-authored
├── user-info/             ← user-authored
├── setup/                 ← package-shipped (refresh overwrites)
├── cv-template/           ← package-shipped (refresh overwrites)
├── .gitignore             ← seeded by init; ignores `.runtime-data/`
├── results/               ← user-consumed, synced
├── applications/          ← user-consumed
└── .runtime-data/         ← pipeline-written, synced via parent
    ├── seen.json          (renamed from `.seen.json`)
    ├── extracts.json
    ├── logs/
    ├── failures/
    └── .cron.lock
```

`results/` and `applications/` stay at root — they are user-consumed artifacts, not run-internal state. Sync behavior is unchanged: the parent `application-pipeline/` directory is the Syncthing folder, so `.runtime-data/` and everything inside it rides the same channel.

`.seen.json` is renamed to `seen.json` on the move — the leading dot was load-bearing only for hiding at the settings-dir root, and is redundant inside a dot-prefixed parent.

## Why

- **Operator hygiene.** The settings-dir root now contains *only* what the operator authored (`config.py`, `user-info/`), what the package shipped (`setup/`, `cv-template/`), or what the operator consumes (`results/`, `applications/`). Everything pipeline-written is one directory away from the things a human edits, and the dot-prefixed name signals "don't touch" without needing a comment.
- **The reset-survival argument from ADR-0011 still holds.** `seen.json` still survives a `mv results results.archive` reset gesture — `.runtime-data/` is a sibling of `results/`, not nested inside it.
- **No new sync policy.** ADR-0002 (`.seen.json` durable via Syncthing) and ADR-0010 (failure files as Syncthing files) are preserved verbatim with a path update; the parent is the synced unit and contents ride it.

## Considered alternatives

- **`var/` or `runtime/` as the subdir name.** `var/` is the Unix convention and short, but the operator-hygiene goal asks for a name that announces "do not touch" without Unix literacy; `runtime/` lacks the visual cue. `.runtime-data/` is dotted (hides from `ls`), explicit about content, and reads as inviolate at a glance.
- **Keep `.seen.json` with its leading dot inside the new subdir.** Rejected: the dot was a root-level hiding mechanism; once the parent already hides, the file-level dot is vestigial and slightly misleading ("hidden among hidden"). One-shot rename, no code shim needed.
- **Auto-move existing files on startup.** Rejected: the deployment is a single operator on a single Pi; a manual `mv` is faster than maintaining a migration shim that has to be deleted later. No guard either — if the operator forgets, the next cron tick starts a fresh `seen.json` and re-classifies the backlog (expensive but recoverable).
- **Wipe (ADR-0024 precedent).** Rejected: ADR-0024's wipe was justified by a *schema* change where interpretation was hard; here the bytes are unchanged and only the path moves. Wipe would discard a pool that has real LLM-cost embedded in it for no schema reason.
- **Split sync (state synced, logs not).** Rejected: log volume on a once-a-day cron is small; split-sync would require Syncthing reconfiguration on every host for no operational gain.

## Consequences

- **`Config` / `DataPaths` (`config/types.py`)** rebase all five derived paths (`seen_store_path`, `results_dir`, `failures_path`, `logs_path`, plus extracts) under `<data_dir>/.runtime-data/` — except `results_dir`, which stays at `<data_dir>/results/`. `seen.json` loses its leading dot. The `.cron.lock` path (currently `<data_dir>/.cron.lock` in `cron.sh`) moves to `<data_dir>/.runtime-data/.cron.lock`.
- **Lazy `mkdir`.** `.runtime-data/` is created on first write by whichever component writes first (dedup store, log writer, failure writer, cron flock). No eager bootstrap in `init`.
- **`init --refresh` does not touch `.runtime-data/`.** Same boundary as `user-info/` and `config.py`: refresh overwrites only package-owned scaffolding (`setup/`, `cv-template/`, the four package-owned Claude skill dirs per ADR-0044). Operator state is never deleted by an upgrade.
- **`init` seeds a `.gitignore` at the settings-dir root** containing `.runtime-data/`, so operators who place their settings dir under their own git get the boundary for free. Skip-if-present like every other seeded file.
- **Manual migration.** Existing installs `mv .seen.json .runtime-data/seen.json`, `mv extracts.json failures logs .runtime-data/` by hand on the next upgrade. No guard, no warning, no auto-move. If forgotten, the pipeline silently starts a fresh `seen.json` and re-classifies on the next tick.
- **Repo `.gitignore`** drops the obsolete `.seen.json` line (the wholesale `application-pipeline/` ignore already covers the new path).
- **CONTEXT.md** updates all path examples and the `Daily Results File` / `Failure Report` / `Log Artifacts` / `Deduplication` / `Atomic Write Helper` / `Results File Manager` / `Run Log` entries to reference `.runtime-data/` where appropriate.

## Supersedes / amends

- **Amends ADR-0011.** The "no `data/` segment in the path; the settings directory *is* the data directory" claim is narrowed: it still holds for *user-facing* paths (`config.py`, `user-info/`, `results/`, `applications/`) but pipeline-written paths now live one segment deeper under `.runtime-data/`. The canonical layout block in ADR-0011 is now stale; this ADR's layout supersedes it. `init --refresh`'s package-owned-only rule from ADR-0011 is preserved.
- **Amends ADR-0002.** `.seen.json` at `data/.seen.json` becomes `seen.json` at `data/.runtime-data/seen.json`. Single-writer Pi, Syncthing-mirrored laptop, sibling-to-`results/` reset-survival — all preserved.
- **Amends ADR-0010.** Failures still surface as files under `failures/`; the directory moves from `<data_dir>/failures/` to `<data_dir>/.runtime-data/failures/`. Acknowledge-by-delete protocol unchanged.
- **Amends ADR-0018 / ADR-0045.** Log artifact paths rebase from `<data_dir>/logs/` to `<data_dir>/.runtime-data/logs/`. Layer-subdir layout and identifier-prefix rules unchanged.
