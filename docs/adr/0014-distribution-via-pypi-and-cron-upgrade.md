# Distribution: PyPI package, cron upgrades-and-runs each tick

`application_pipeline` distributed as PyPI package. Seeded `cron.sh` does `pip install --upgrade` (twice for CDN lag), `init --refresh`, then `run`. Publish: push to `main` → TestPyPI via OIDC; `v*.*.*` tag → PyPI.

## Why

- One-line `pip install --upgrade` replaces git-tag choreography. Trusted publishing via OIDC keeps tokens out of repo secrets.

## Consequences

- Retires git-tag pull model. `init --refresh` for cron self-healing. pip-upgrade failures warn-and-continue.
- Cron `30 0 * * 1-5` hardcoded by `cron-install.sh`.
