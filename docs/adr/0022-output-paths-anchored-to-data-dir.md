# Output paths anchored to `data/`, derived from `config.py`'s parent

All pipeline output and state paths — the **Results File**, the **Deduplication** store (`.seen.json`), **Failure Reports**, and per-component logs — are derived at load time from the parent directory of the loaded `config.py`. That directory is named `data/` on disk (not `data/synched/`); the in-code variable is `data_dir`. The canonical layout under the synced folder is:

```
~/application-pipeline/data/
├── config.py
├── layout.py
├── user-info/
├── prompts/
├── .seen.json
├── results/
│   └── current.md
├── failures/
└── logs/
```

`.seen.json` sits at `data/.seen.json` — sibling to `data/results/`, not inside it. The `SEEN_STORE_PATH` env-var and module-attr override are removed; no new `RESULTS_PATH` / `FAILURES_PATH` knobs are introduced. The two literal-relative paths that caused issue #297 (`orchestrator.py`'s `Path("results/current.md")` and `__main__.py`'s `Path("results")` for failures) are replaced by `data_dir`-derived paths.

## Why

- **The bug was structural, not local.** Issue #297 found `.seen.json` in three CWD-dependent locations and `results/current.md` in a fourth — because two relative paths resolved against the process CWD instead of being anchored. The same anchoring pattern (`config_dir / <subpath>`) already governs `LAYOUT`, `USER_INFO_DIR`, `CLASSIFY_RELEVANCE_PROMPT`, and `JUDGE_MATCH_PROMPT` in the **Config Loader**. Extending it to results and seen state closes the bug class, not just the two known sites.
- **One mental model survives.** ADR-0013's rationale ("the synced folder answers both 'where do I edit settings?' and 'where do I read results?'") was always the right shape. This ADR keeps that property and removes the transport-aware name that suggested otherwise.
- **The on-disk name shouldn't bake in the transport.** "Synched" is a Syncthing fact about the Pi deployment; on a laptop dev box or a future non-Syncthing transport, the folder still exists and still holds the same content. `data/` describes what it *is*, not how it's mirrored.
- **`.seen.json` outside `results/` survives a results-dir reset.** The **Results File** entry in CONTEXT.md describes reset as "moving/deleting the file"; an operator who one day reaches for `mv data/results data/results.archive` should not lose dedup memory. Placing `.seen.json` at the data root makes that gesture safe.
- **No override knobs is simpler.** ADR-0013 #26 already states "the Pi has exactly one deployment shape." Override knobs for `SEEN_STORE_PATH` / `RESULTS_PATH` / `FAILURES_PATH` add configuration surface for a deployment that doesn't vary. Removing the only existing one (`SEEN_STORE_PATH`) makes the loader symmetric across all path-typed fields.

## Considered alternatives

- **Keep `synched/` as the on-disk subdirectory name; only rename the in-code variable** — rejected. The runbook, log lines, and shell paths the operator types all surface the directory name. Calling it `data_dir` in Python while the disk says `synched/` is exactly the symmetry break that produced this issue.
- **Flat layout: `data/current.md`, `data/.seen.json` with no `results/` subdir** — rejected. The `results/` subdir gives a natural home for future per-run artifacts (archives, dated snapshots) without a second namespace decision later. The cost is one extra path segment; the upside is room to grow.
- **`.seen.json` inside `results/` (literal reading of ADR-0002's "alongside the Results File")** — rejected. The colocation rationale was about transport (sync via the same Syncthing folder), not directory adjacency. Durability across a results-dir reset is the stronger property; the new layout interprets "alongside" as "in the same synced folder."
- **Introduce `Config.data_dir` as an explicit field** (default `pathlib.Path(__file__).parent`) — rejected. The only valid value is the parent of the loaded `config.py`; making it user-settable lets the operator misconfigure the very invariant the loader needs to preserve. `__file__` semantics inside an `importlib`-loaded user module are fragile.
- **Code-driven migration of stale `.seen.json` and `results/current.md` locations** — rejected. Exactly one Pi deployment is in flight and ADR-0013 #28 already accepts a manual re-pair as part of the rename history. The migration is a one-shot HITL slice (in issue #297) rather than code that exists once and then never runs again.

## Consequences

- **`pi-tick.sh` flips `SYNCHED_DIR` → `DATA_DIR`** and points at `${BASE_DIR}/data`. `FAILURES_DIR`, the `init` arg, and the config-path arg all move up one level.
- **The Syncthing pairing on the Pi re-points** at `~/application-pipeline/data/` instead of `~/application-pipeline/data/synched/`. The single in-flight deployment (#120) follows the HITL migration in issue #297.
- **ADR-0013's rename clause (`results/` → `synched/`) is superseded.** The settings-co-located-with-outputs rationale, the `init` story, and the atomic-deploy-survival property stand unchanged — only the on-disk directory name is reverted to a transport-neutral `data/`.
- **ADR-0002's "alongside the Results File" is amended** to "inside the synced folder, sibling to the results subdir." Durability survives a results-dir reset.
- **CONTEXT.md path references flip**: `data/synched/` → `data/`, `synched/current.md` → `data/results/current.md`, `synched/failures/` → `data/failures/`, `synched/logs/` → `data/logs/`. The **Results File Manager** entry's stale `hardcoded results/current.md` line is corrected to reflect the `data_dir / results / current.md` derivation.
- **The `SEEN_STORE_PATH` env-var and module-attribute override are removed.** The **Config Loader** no longer reads `os.environ["SEEN_STORE_PATH"]` and no longer consults `module.SEEN_STORE_PATH`. The seen-store path is `data_dir / ".seen.json"`, period.
- **`__main__.py`'s `Path("results")` failure-destination literal is replaced** by `data_dir / "failures"` (the same derivation as the orchestrator), folding in the latent-bug flag ADR-0013 #33 left for a follow-up.
- **No new ADR is needed** for future path additions under `data/`: the rule is "everything under the synced folder is anchored to `data_dir`."
