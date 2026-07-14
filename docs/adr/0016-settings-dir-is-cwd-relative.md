# Settings dir is CWD-relative; no env-var override

Settings directory hardcoded to `Path.cwd() / "application-pipeline"`. No `APPLICATION_PIPELINE_HOME` env-var, no walk-up. Mirrors pycastle's CWD-relative pattern.

## Why

- One mental model across pycastle and application-pipeline. No silent env-var footguns.

## Consequences

- `run` and `compile-cv` fail loud-and-fast (exit 2) if missing.
- `cron.sh` self-locates via `$(dirname "$0")/../..`.
