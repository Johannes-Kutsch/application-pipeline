# User settings in a flat settings directory; all output paths anchored to it

The two user-editable Python modules — **Config** (`config.py`) and **Layout** (`layout.py`) — live in a single flat settings directory. The directory's **location** is now hardcoded to `<cwd>/application-pipeline/` (ADR-0029, supersedes the original "user picks at init time, conventionally `~/application-pipeline/`" claim); the **shape** below is unchanged. Default contents ship inside the package at `src/application_pipeline/templates/` and are materialised by `application-pipeline init <dir>`. There is no `data/` segment in the path; the settings directory *is* the data directory.

All output and state paths — the **Daily Results File**, `.seen.json`, **Failure Reports**, per-component logs, `extracts.json` — are derived at load time from the parent directory of the loaded `config.py` (`data_dir`). Canonical layout:

```
~/application-pipeline/
├── config.py
├── layout.py
├── user-info/        ← Triage Profile markdown + LaTeX facts/content fragments
├── skills/           ← Skill-shipped scaffolding (e.g. cv_skeleton.tex) — refreshable per ADR-0030
├── setup/            ← cron.sh, cron-install.sh, cron-uninstall.sh
├── .seen.json
├── extracts.json
├── applications/     ← per-listing <app_dir> folders (cv.tex + .build/ + PDFs)
├── results/YYYY-MM-DD.md
├── failures/
└── logs/
```

LaTeX class files (`moderncv.cls`, `.sty`s) ship inside the package at `src/application_pipeline/latex/` and are copied into the per-listing `<app_dir>/.build/` at compile time; they no longer live in the settings dir.

No `SEEN_STORE_PATH`/`RESULTS_PATH`/`FAILURES_PATH` override knobs. `.seen.json` sits at the root — sibling to `results/`, not inside it, so it survives a `mv results results.archive` reset gesture.

## Why

- **Edit on the laptop, propagate to the host.** Settings can ride a Syncthing folder if the user wants two-host editing; nothing in the layout forces this.
- **Templates inside the package travel with the code.** Schema changes ship in the same release as the template that demonstrates them. `importlib.resources` is the right idiom.
- **One mental model.** The settings folder answers both "where do I edit settings?" and "where do I read results?"
- **No `data/` segment is simpler.** The previous nested shape inherited a synced-folder-vs-data-folder distinction that no longer exists once the package is PyPI-distributed (ADR-0027). One root, one anchor.
- **No override knobs is simpler.** One deployment shape. Path-typed Config fields are symmetric (`config_dir / <subpath>`).

## Considered alternatives

- **Loader auto-materialises on missing file** — rejected: silent I/O writes surprise laptop dev users with typo'd config paths.
- **Refuse-on-conflict for `init`** — rejected: disaster recovery (host re-image, restore from backup) would error on the init step. Skip-existing is idempotent.

## Consequences

- **`application-pipeline init`** writes `config.py`, `layout.py`, the `user-info/` files, the `skills/` scaffolding (e.g. `cv_skeleton.tex`), and the `setup/*.sh` scripts via `importlib.resources`. Skip-existing per file; prints `wrote <file>` / `skipped <file>`; exits 0 even when all are skipped. Per ADR-0027, the cron wrapper invokes `init` on every tick so new template files added in a release self-heal onto the host without manual intervention. **`init --refresh` overwrites `setup/*.sh` and `skills/cv_skeleton.tex` unconditionally** (per ADR-0030 — both are package-shipped scaffolding structurally tied to package code); everything else stays seed-if-missing.
- **Package templates are runnable as shipped** with placeholder SWE keywords/skills so the first cron tick produces a non-empty daily file (proving the plumbing).
- **Disaster recovery:** `config.py`/`layout.py` ride whichever sync channel the user configures (or a manual restore); `init` skips both on the recovered host.
