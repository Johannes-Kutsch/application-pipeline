# Cron setup

## Installing the cron job

After `application-pipeline init`, run:

```bash
bash <cwd>/application-pipeline/setup/cron-install.sh
```

This writes a single crontab line for the current user:

```
30 0 * * 1-5 /path/to/<cwd>/application-pipeline/setup/cron.sh >> /path/to/<cwd>/application-pipeline/.runtime-data/logs/cron.log 2>&1 # application-pipeline:/path/to/<cwd>/application-pipeline
```

The comment tag (`# application-pipeline:<absolute-settings-path>`) allows multiple independent
installs on the same host to coexist — each install manages its own tagged line.

Cron fires **weekdays at 00:30 local time** (ADR-0017). Each tick:

1. Checks `.venv/` exists (fails if not).
2. Upgrades the package (`.venv/bin/pip install --upgrade application-pipeline`, run twice to absorb
   PyPI CDN propagation lag). Best-effort — a failed upgrade does not abort the tick.
3. Self-heals new template files (`application-pipeline init --refresh`).
4. Runs the pipeline (`application-pipeline run`).

No flock — single-writer on the Pi, overlapping ticks cannot occur.

## Removing the cron job

```bash
bash <cwd>/application-pipeline/setup/cron-uninstall.sh
```

This removes the tagged crontab line without affecting any other crontab entries.

## Optional Syncthing

If you want to read Daily Results Files and logs on another device, configure Syncthing to sync the
settings folder (or just `results/` and `logs/`). The pipeline is single-writer at the process
level — Syncthing propagates files outward read-only. No special configuration is needed: the daily
file is fsynced before the run exits, so Syncthing picks up a complete file rather than a partial
write.

Failure Reports (`failures/<timestamp>.md`) also propagate via Syncthing. Acknowledge a failure by
deleting the file.

## Releasing

Publishing a new version to PyPI requires a one-time Trusted Publisher setup on both PyPI and
TestPyPI. This is a manual step performed once per project, not per release.

**TestPyPI** (receives every push to `main`):

1. Create a TestPyPI account and project named `application-pipeline`.
2. In the project's Publishing settings, add a Trusted Publisher:
   - Repository: `Johannes-Kutsch/application-pipeline`
   - Workflow: `publish.yml`
   - Environment: `testpypi`

**PyPI** (receives every `v*.*.*` tag):

1. Create a PyPI account and project named `application-pipeline`.
2. In the project's Publishing settings, add a Trusted Publisher:
   - Repository: `Johannes-Kutsch/application-pipeline`
   - Workflow: `publish.yml`
   - Environment: `pypi`

After setup, releases are keyless — no API tokens in repository secrets. Push a `v*.*.*` tag on a
clean commit (no `.dev` suffix in the setuptools-scm computed version) to trigger a PyPI release.
Every push to `main` publishes to TestPyPI so packaging regressions are caught before tagging.
