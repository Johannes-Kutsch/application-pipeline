# Pi deploys atomically via staging clone + symlink flip

Each release tag is materialized into its own `releases/<tag>/` with its own venv; the cron wrapper installs and smoke-tests, then atomically flips the `current` symlink. Cron invariably executes `~/application-pipeline/current/.venv/bin/python -m application_pipeline`.

## Why

- **Update-mid-cron-tick must not strand the Pi.** A `git checkout && pip install -e .` inline with cron leaves source on the new version and entry points on the old one if install fails.
- **Rollback is a symlink flip, not a git operation.** `ln -sfn releases/v1.1.2 current` — fast, atomic, no re-install.
- **Deploy and run paths share no mutable state.** Wrapper writes to `releases/<new-tag>/`; running pipeline reads from `current/`. No half-installed window.

## Consequences

- Disk layout: `~/application-pipeline/{current -> releases/<tag>, releases/<tag>/{.venv,src,...}}`.
- Wrapper sequence: clone tag → create `.venv` → `pip install -e .` → smoke test (`python -c "import application_pipeline"`) → atomic `ln -sfn` → invoke run. Any failure before the symlink flip writes a Failure Report (ADR-0010); previous `current` untouched.
- Retention: keep last N=3 releases for rollback; older directories pruned by the wrapper.
- Crontab references `current/` symlink — never a specific tag.
- Setup runbook bootstraps the `releases/` and `current` layout before the first cron tick.
