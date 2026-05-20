# Settings dir is CWD-relative; no env-var override, no auto-discovery

The settings directory ("home dir") is hardcoded to `Path.cwd() / "application-pipeline"`. The three subcommands no longer accept a settings-dir argument:

- `application-pipeline run` reads `<cwd>/application-pipeline/config.py`.
- `application-pipeline init [--refresh]` seeds into `<cwd>/application-pipeline/`.
- `application-pipeline compile-cv <app_dir>` compiles a per-listing draft in `<app_dir>` and reads `user-info/` from `<cwd>/application-pipeline/user-info/`. (The `<app_dir>` argument is the per-listing application folder, unchanged from the prior shape.)

`run` and `compile-cv` fail loud-and-fast at the CLI boundary (exit 2, named error: `"no application-pipeline/config.py in <cwd> — did you forget to cd, or run init?"`) if the settings dir is missing. `init` creates it.

There is **no `APPLICATION_PIPELINE_HOME` env-var override and no auto-discovery walk-up.** The previous half-implementation in `compile_cv_cmd.py` is removed.

This mirrors pycastle's `Path.cwd() / "pycastle"` pattern (`src/pycastle/config/loader.py`).

## Why

- **One mental model across pycastle and application-pipeline.** Both projects live as subdirectories of the same `.venv`-bearing repo root; same invocation rule ("cd to the repo, run the command") works for both.
- **No silent footguns from env-var precedence.** A stale `APPLICATION_PIPELINE_HOME` in a shell rc file would silently redirect writes; the hardcoded path can't.
- **Failure mode is locally diagnosable.** The loud error names both fixes the user might need (`cd`, or `init`). No "why are my results going to the wrong place" debugging.
- **Cron is simpler.** `cron.sh` self-locates via `$(dirname "$0")/../..` and `cd`s to the project root; the cron line carries only the absolute path to the script. No path-substitution at install time.

## Considered alternatives

- **Venv-relative (`Path(sys.executable).parents[2]`).** Rejected: stable across CWD but breaks under `pipx`/global installs, and diverges from pycastle.
- **Walk-up from CWD looking for an `application-pipeline/` marker.** Rejected: more magic than needed; the failure mode ("no marker found anywhere above CWD") is harder to diagnose than the flat "not in this directory" check.
- **Keep `APPLICATION_PIPELINE_HOME` env-var as override.** Rejected: the only callers were `compile_cv_cmd.py` (transitional) and hypothetical tests; tests can pass paths through internal APIs. Removing one surface to document.
- **Soft cutover with deprecation warning on legacy `~/application-pipeline/`.** Rejected: install base is tiny (operator's Pi + laptop), and a one-line `mv` + crontab edit is faster than a deprecation path that lives forever.

## Consequences

- **Hard cutover for existing installs.** First post-upgrade run from the wrong CWD prints the loud error. Operator moves the old settings dir into the new project root (`mv ~/application-pipeline <repo>/application-pipeline`), re-runs `bash application-pipeline/setup/cron-install.sh` from the new location to refresh the crontab line.
- **Cron template changes** (ADR-0024 amended): cron line is `30 0 * * 1-5 <repo>/application-pipeline/setup/cron.sh ...`; `cron.sh` `cd`s to `$(dirname "$0")/../..` before invoking any subcommand.
- **`compile_cv_cmd.py` simplifies.** The env-var lookup and `~/application-pipeline` fallback are deleted; `user-info/` is resolved as `Path.cwd() / "application-pipeline" / "user-info"`.
- **`__main__.py` grows a precheck** for `run` and `compile-cv`: assert `Path.cwd() / "application-pipeline" / "config.py"` exists, else exit 2 with the named error.
- **`init` no longer takes a positional dir.** `application-pipeline init` and `application-pipeline init --refresh` are the only invocations.
- **CONTEXT.md and ADR-0011 amended** to point at this ADR for the settings-dir location; the layout (subpaths under the settings dir) is unchanged.
