# User settings in a flat settings directory; all output paths anchored to it

The two user-editable Python modules — **Config** (`config.py`) and **Layout** (`layout.py`) — live in a single flat settings directory the user picks at `init` time (conventionally `~/application-pipeline/`). Default contents ship inside the package at `src/application_pipeline/templates/` and are materialised by `application-pipeline init <dir>`. There is no `data/` segment in the path; the settings directory *is* the data directory.

All output and state paths — the **Daily Results File**, `.seen.json`, **Failure Reports**, per-component logs, `extracts.json` — are derived at load time from the parent directory of the loaded `config.py` (`data_dir`). Canonical layout:

```
~/application-pipeline/
├── config.py
├── layout.py
├── user-info/        ← Triage Profile markdown + LaTeX content fragments
├── latex/            ← CV / cover-letter template + moderncv class files
├── setup/            ← cron.sh, cron-install.sh, cron-uninstall.sh
├── .seen.json
├── extracts.json
├── results/YYYY-MM-DD.md
├── failures/
└── logs/
```

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

- **`application-pipeline init <dir>`** writes `config.py`, `layout.py`, the `user-info/` files, the `latex/` template + class files, and the `setup/*.sh` scripts via `importlib.resources`. Skip-existing per file; prints `wrote <file>` / `skipped <file>`; exits 0 even when all are skipped. Refresh from a newer template: delete and re-run. Per ADR-0027, the cron wrapper invokes `init` on every tick so new template files added in a release self-heal onto the host without manual intervention.
- **Package templates are runnable as shipped** with placeholder SWE keywords/skills so the first cron tick produces a non-empty daily file (proving the plumbing).
- **Disaster recovery:** `config.py`/`layout.py` ride whichever sync channel the user configures (or a manual restore); `init` skips both on the recovered host.
