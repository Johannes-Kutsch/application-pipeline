# Cron setup

## Installing the cron job

After `application-pipeline init <settings-dir>`, run:

```bash
bash <settings-dir>/setup/cron-install.sh
```

This writes a single crontab line for the current user:

```
30 0 * * * /path/to/<settings-dir>/setup/cron.sh >> /path/to/<settings-dir>/logs/cron.log 2>&1 # application-pipeline:/path/to/<settings-dir>
```

The comment tag (`# application-pipeline:<absolute-settings-path>`) allows multiple independent
installs on the same host to coexist — each install manages its own tagged line.

Cron fires **once per day at 00:30 local time**. Each tick:

1. Upgrades the package (`pip install --upgrade application-pipeline`, run twice to absorb PyPI CDN
   propagation lag).
2. Self-heals new template files (`application-pipeline init --refresh <settings-dir>`).
3. Runs the pipeline (`application-pipeline run <settings-dir>/config.py`).

## Removing the cron job

```bash
bash <settings-dir>/setup/cron-uninstall.sh
```

This removes the tagged crontab line without affecting any other crontab entries.

## Flock serialisation

`cron.sh` acquires a global flock at `$SETTINGS_DIR/.cron.lock` before running. If a
previous tick is still executing — for example because it is sleeping through a Claude quota window
(ADR-0023) — the next cron fire waits at the lock rather than spawning a parallel run. The waiting
fire will proceed as soon as the lock is released.

When a run sleeps through quota and crosses midnight, the cron-anchored logical date (the date the
run started) is used for the Daily Results File name, so the file always lands on the correct day
even if the run finishes the following morning.

The operator signal for a missed or sleeping run is the absence of `results/YYYY-MM-DD.md` for a
given day. Check `logs/pipeline_orchestrator.events.jsonl` for the newest row's timestamp to
confirm the run is still alive rather than failed.

## Optional Syncthing

If you want to read Daily Results Files and logs on another device, configure Syncthing to sync the
settings folder (or just `results/` and `logs/`). The pipeline is single-writer at the process
level — Syncthing propagates files outward read-only. No special configuration is needed: the daily
file is fsynced before the run exits, so Syncthing picks up a complete file rather than a partial
write.

Failure Reports (`failures/<timestamp>.md`) also propagate via Syncthing. Acknowledge a failure by
deleting the file.

## Migration from a legacy Pi layout

Earlier deployments used a `~/application-pipeline/{repo,releases,current,data}/` tree managed by a
git-tag pull model. To migrate to the PyPI-based install:

1. Remove the legacy directories:

   ```bash
   rm -rf ~/application-pipeline/repo \
          ~/application-pipeline/releases \
          ~/application-pipeline/current \
          ~/application-pipeline/data
   ```

2. Create a fresh venv and install the package:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install application-pipeline
   ```

3. Seed a new settings folder and arm cron:

   ```bash
   .venv/bin/application-pipeline init ~/application-pipeline/settings
   bash ~/application-pipeline/settings/setup/cron-install.sh
   ```

4. Edit `config.py`, `layout.py`, and the `user-info/` files to match your previous configuration.

No automatic state migration is provided — the first run after cutover processes all sources from
scratch. Expect a higher classifier token volume on the first day; subsequent days return to
steady-state as the Pool builds up.

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
