# Distribution: PyPI package, cron upgrades-and-runs each tick

`application_pipeline` distributed as a PyPI package. Install: `python3 -m venv .venv && .venv/bin/pip install application-pipeline && application-pipeline init && bash setup/cron-install.sh`. Seeded `cron.sh` does `pip install --upgrade` (twice for CDN lag), `init --refresh`, then `run`.

Publish workflow: push to `main` → TestPyPI via OIDC. Push `v*.*.*` tag → PyPI via OIDC. Version computed by setuptools-scm.

## Why

- One-line `pip install --upgrade` replaces git-tag pull + clone + venv + symlink-flip choreography.
- pip's install atomicity is sufficient — one-command rollback by version pin.
- Trusted publishing via OIDC keeps tokens out of repo secrets.
- TestPyPI on every `main` push catches packaging-only regressions.

## Consequences

- Retires the previous git-tag pull model. No `~/application-pipeline/{repo,releases,current}/` tree.
- `init --refresh` mode for cron self-healing of new template files.
- pip-upgrade failures warn-and-continue — no Failure Report, tick proceeds on current version.
- Cron schedule `30 0 * * 1-5` hardcoded by `cron-install.sh` (ADR-0017).
