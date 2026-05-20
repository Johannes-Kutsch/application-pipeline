# User settings in synced folder; all output paths anchored to `data/`

The two user-editable Python modules — **Config** (`config.py`) and **Layout** (`layout.py`) — live on the Pi at `~/application-pipeline/data/{config,layout}.py`. Default contents ship inside the package at `src/application_pipeline/templates/` and are materialised by `python -m application_pipeline init <dir>`.

All output and state paths — the **Daily Results File**, `.seen.json`, **Failure Reports**, per-component logs, `data/extracts.json` — are derived at load time from the parent directory of the loaded `config.py` (`data_dir`). Canonical layout:

```
~/application-pipeline/data/
├── config.py
├── layout.py
├── user-info/
├── .seen.json
├── extracts.json
├── results/YYYY-MM-DD.md
├── failures/
└── logs/
```

No `SEEN_STORE_PATH`/`RESULTS_PATH`/`FAILURES_PATH` override knobs. `.seen.json` sits at `data/.seen.json` — sibling to `data/results/`, not inside it, so it survives a `mv data/results data/results.archive` reset gesture.

## Why

- **Edit on the laptop, propagate to the Pi.** Settings in the Syncthing folder are picked up by the next cron tick without SSH.
- **Surviving atomic deploys is automatic.** Synced folder sits outside `releases/v*/`; per-tag symlink flips never touch user settings.
- **Templates inside the package travel with the code.** Schema changes ship in the same release as the template that demonstrates them. `importlib.resources` is the right idiom.
- **One mental model.** The synced folder answers both "where do I edit settings?" and "where do I read results?"
- **`data/` describes what it is, not how it's mirrored.** Transport-neutral on disk; the previous `synched/` naming baked Syncthing into the path.
- **No override knobs is simpler.** One deployment shape. Path-typed Config fields are symmetric (`config_dir / <subpath>`).

## Considered alternatives

- **Loader auto-materialises on missing file** — rejected: silent I/O writes surprise laptop dev users with typo'd config paths.
- **Refuse-on-conflict for `init`** — rejected: disaster recovery (Pi re-image; Syncthing restores from laptop) would error on the init step. Skip-existing is idempotent.

## Consequences

- **`python -m application_pipeline init <dir>`** writes `config.py`, `layout.py`, and the `user-info/` files via `importlib.resources`. Skip-existing per file; prints `wrote <file>` / `skipped <file>`; exits 0 even when all are skipped. Refresh from a newer template: delete and re-run.
- **`pi-tick.sh`** hardcodes `DATA_DIR="${BASE_DIR}/data"` and invokes `exec ".../python" -m application_pipeline "${DATA_DIR}/config.py"`. Wrapper-level config knobs are deliberately not offered.
- **Package templates are runnable as shipped** with placeholder SWE keywords/skills so the first cron tick produces a non-empty daily file (proving the plumbing).
- **Disaster recovery:** `config.py`/`layout.py` ride the Syncthing channel and come back automatically; `init` skips both on the recovered Pi.
