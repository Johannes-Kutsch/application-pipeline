# User settings in a flat settings directory; all output paths anchored to it

User-editable **Config** (`config.py`) lives in a settings directory hardcoded to `<cwd>/application-pipeline/` (ADR-0022). Default contents ship inside the package at `src/application_pipeline/templates/` and are materialised by `application-pipeline init`. No `data/` segment; the settings directory *is* the data directory.

All output and state paths — **Daily Results File**, `seen.json`, **Failure Reports**, logs, `extracts.json` — derived from the parent directory of `config.py` (`data_dir`). Pipeline-written paths live under `.runtime-data/` (ADR-0037).

**Amendment: no path override knobs at all.** `USER_INFO_DIR`, `LAYOUT`, prompt path knobs all retired. `user-info/` is canonical under the settings dir. All go into `_REMOVED_FIELDS` for loud-fail on stray knobs.

## Why

- One mental model: settings folder answers "where to edit" and "where to read results."
- Templates inside the package travel with the code via `importlib.resources`.
- One deployment shape, one anchor — no override surface.

## Consequences

- `application-pipeline init` writes via `importlib.resources`. Skip-existing per file. `init --refresh` overwrites `setup/*.sh` and package-owned scaffolding only; user-authored files safe across upgrades.
- Disaster recovery: `config.py` rides the sync channel; `init` skips it on the recovered host.
