# Pi deploys atomically via staging clone + symlink flip

The Pi never runs the pipeline from a half-installed state. Each release tag is materialized into its own `releases/<tag>/` directory with its own virtualenv; the cron wrapper installs and smoke-tests the new release, and only on success atomically flips the `current` symlink to point at it. Cron invariably executes `~/application-pipeline/current/.venv/bin/python -m application_pipeline`.

## Why

- **Update-mid-cron-tick must not strand the Pi.** A `git checkout && pip install -e .` inline with `cron` leaves the on-disk source on the new version but the installed entry points on the old one if `pip install` fails (network blip, dep resolution conflict, broken `pyproject.toml`). Every subsequent tick inherits the same broken state until manual intervention.
- **Rollback is a symlink flip, not a git operation.** When `v1.1.3` misbehaves, restoring `v1.1.2` is `ln -sfn releases/v1.1.2 current` — fast, atomic, and doesn't require re-running `pip install`.
- **The deploy and run paths share no mutable state.** The wrapper writes to `releases/<new-tag>/`; the running pipeline reads from `current/`. There is no window in which a running invocation could pick up half-installed dependencies.

## Considered alternatives

- **Bash short-circuit (`git checkout && pip install && run`)** — rejected: half-installed states leak across ticks; recovery requires SSH.
- **Tag pin in a state file (last-known-good)** — rejected: equivalent durability to the symlink, weaker atomicity (file writes are atomic with `os.replace`, but two consumers reading at different moments could disagree). The symlink is what unix already gives us for this exact problem.
- **`pip install` against a shared venv with in-place upgrade** — rejected: in-place dep upgrades can produce broken intermediate states visible to a concurrent reader. With per-release venvs the running pipeline's deps are immutable for the duration of the run.

## Consequences

- **Disk layout on the Pi:**
  ```
  ~/application-pipeline/
    current        -> releases/v1.1.3
    releases/
      v1.1.2/  (kept for rollback)
      v1.1.3/
        .venv/
        src/
        ...
  ```
- **Wrapper sequence**: clone target tag into `releases/<tag>/`, create `.venv` inside it, `pip install -e .`, run a smoke test (`python -c "import application_pipeline"`), atomically `ln -sfn releases/<tag> current`, then invoke the run. If any step before the symlink flip fails, write a failure report (per ADR-0012) and exit; the previous `current` symlink is untouched.
- **Retention**: keep the last N=3 releases for rollback. Older release directories are pruned by the wrapper.
- **Crontab** invokes `~/application-pipeline/current/.venv/bin/python -m application_pipeline` — never references a specific tag directly.
- **Setup runbook** must create the `releases/` and `current` symlink layout on the first install (bootstrap clones the initial tag into `releases/<tag>/` and creates the symlink before the first cron tick).
