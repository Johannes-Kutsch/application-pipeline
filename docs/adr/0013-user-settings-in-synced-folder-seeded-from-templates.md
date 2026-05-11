# User settings live in the synced folder, seeded from in-package templates

The two user-editable Python modules — **Config** (`config.py`) and **Layout** (`layout.py`) — live on the Pi at `~/application-pipeline/data/synched/{config,layout}.py`, inside the Syncthing-paired folder. Default contents ship inside the installed package at `src/application_pipeline/templates/{config,layout}.py` and are materialised onto the Pi by `python -m application_pipeline init <dir>`. The `settings/` directory at the repo root is removed; the Pi's synced folder is renamed `results/` → `synched/` to reflect that it now carries inputs as well as outputs.

## Why

- **Edit on the laptop, propagate to the Pi.** Putting Config and Layout in the Syncthing folder means the applicant edits them in their normal editor on their normal machine; the next cron tick picks up the change without SSH. This is what #121 asked for, generalised to layout as well.
- **Surviving atomic deploys is automatic.** The synced folder sits outside `releases/v*/` (per ADR-0011), so per-tag symlink flips never touch user settings.
- **Templates inside the package travel with the code.** Schema changes to the loader (new required fields, renamed options) ship in the same release as the template that demonstrates them. The runbook never needs to be re-edited to track template changes.
- **`importlib.resources` is the right idiom.** Treating templates as package data — not as repo-root files copied by shell — means the bootstrap step doesn't have to know the `releases/v*/src/application_pipeline/...` path and works identically whether the package is installed editable, wheel, or sdist.
- **One folder, one mental model.** The synced folder now answers both "where do I edit settings?" and "where do I read results?" — symmetry that the old name `results/` couldn't carry.

## Considered alternatives

- **Config in `settings/config.py` at the repo root** (the original glossary position) — rejected: a personal `KEYWORDS`/`SKILLS` list doesn't belong in a public repo, and committing it forces every personalisation through a tag-and-deploy cycle.
- **Config in `~/application-pipeline/data/config.py`, sibling of (but outside) the synced folder** (issue #122's initial sketch) — rejected: requires the operator to remember which files cross the sync boundary and which don't; doesn't address #121's "edit on laptop" request.
- **Config in `~/application-pipeline/current/settings/config.py`, inside the release tree** — rejected: wiped on every atomic deploy. Could work for `layout.py` (which is currently checked-in and survives because the user re-commits between tags) but breaks for a per-Pi `config.py`.
- **Loader auto-materialises on missing file** — rejected: makes `load()` do silent I/O writes, surprises laptop dev users whose typo'd config path conjures a generic-SWE file in their working directory.
- **`pi-tick.sh` self-heals by copying the template if `config.py` is missing** — rejected: pushes deployment-shape knowledge (template paths inside the package) into shell, and the bootstrap event becomes invisible — operator only learns "config got created with defaults" by reading cron logs.
- **Refuse-on-conflict semantics for `init` (with or without `--force`)** — rejected: disaster recovery (Pi re-image; Syncthing restores `synched/` from laptop) becomes a path where the init step errors out, requiring the runbook to special-case it. Skip-existing is idempotent across both first-run and post-restore.
- **Land the `synched/` rename as a separate PR** — rejected: the new name is only descriptive in the new world where Config and Layout live there. Splitting the PR briefly leaves the runbook self-contradictory (a folder called `results/` containing input config).

## Consequences

- **CLI surface gains an `init` mode**: `python -m application_pipeline init <dir>` writes `config.py` and `layout.py` into `<dir>` via `importlib.resources.files("application_pipeline.templates")`. Skip-existing per file; prints `wrote <file>` / `skipped <file> (already exists)` for each; exits 0 even when both are skipped. To refresh from a newer template, the operator deletes the target file and re-runs.
- **`pi-tick.sh` hardcodes the config path**: `exec ".../python" -m application_pipeline "${SYNCHED_DIR}/config.py"` where `SYNCHED_DIR="${BASE_DIR}/data/synched"`. Wrapper-level config knobs are explicitly *not* offered — the Pi has exactly one deployment shape.
- **The package's templates are runnable as shipped**, with placeholder-but-plausible SWE keywords/skills so the first cron tick on a freshly-bootstrapped Pi produces a non-empty `current.md` (proving the plumbing) before the operator personalises.
- **Syncthing folder path changes**: the laptop's existing pairing (if any) points at `data/results/`. Existing Pi deployments (#120 is the only one in flight; currently blocked by the bug this ADR resolves) re-pair against `data/synched/`. Acceptable because no production Pi exists yet.
- **CONTEXT.md updates**: **Config** and **Layout** entries point to `data/synched/{config,layout}.py` on the Pi (seeded from `src/application_pipeline/templates/`). The **Results File** entry's directory reference flips to `synched/current.md`. The **Failure Report** entry flips to `synched/failures/`. The `settings/` directory is no longer mentioned.
- **Runbook structure**: a new section between "Syncthing pairing" (§4) and "Repo bootstrap" / "Initial release" inserts the `init` step after the venv exists and after `data/synched/` is paired but before the crontab is installed.
- **Disaster recovery**: the section on restoring `.seen.json` after Pi disk failure is extended — `config.py` and `layout.py` ride the same Syncthing channel and come back automatically; `init` skips both on the recovered Pi.
- **#121 is closed by this work**, not a follow-up.
- **Latent issue flagged separately**: `__main__.py`'s hardcoded `Path("results")` for failure-report destination resolves against CWD, not the config dir — pre-existing bug, surfaced by this rename. Fix scope: derive the failures path from the config file's parent. Decided here only that the rename touches it; the deeper "failures path derivation" cleanup may land in a follow-up.
