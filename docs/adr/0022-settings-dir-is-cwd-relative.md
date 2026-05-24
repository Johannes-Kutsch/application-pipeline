# Settings dir is CWD-relative; no env-var override

Settings directory hardcoded to `Path.cwd() / "application-pipeline"`. Three subcommands take no settings-dir argument. `run` and `compile-cv` fail loud-and-fast (exit 2) if missing. No `APPLICATION_PIPELINE_HOME` env-var, no auto-discovery walk-up. Mirrors pycastle's CWD-relative pattern.

## Why

- One mental model across pycastle and application-pipeline — same invocation rule.
- No silent footguns from env-var precedence.
- Failure mode is locally diagnosable — the error names both fixes (`cd` or `init`).
- Cron is simpler: `cron.sh` self-locates via `$(dirname "$0")/../..`.

## Consequences

- Hard cutover for existing installs. Operator moves old settings dir, re-runs `cron-install.sh`.
- `init` no longer takes a positional dir argument.
