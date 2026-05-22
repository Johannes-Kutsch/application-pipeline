# Distribution: PyPI package, install into a project-local venv, cron upgrades-and-runs each tick

`application_pipeline` is distributed as a PyPI package. The host install path is: `python3 -m venv .venv`; `.venv/bin/pip install application-pipeline`; `.venv/bin/application-pipeline init <dir>`; `bash <dir>/setup/cron-install.sh`. The seeded `cron.sh` does `pip install --upgrade application-pipeline` (twice, to absorb PyPI CDN propagation lag), then `application-pipeline init --refresh <dir>`, then `application-pipeline run <dir>/config.py`. The cron line is tagged with a per-installation marker (`# application-pipeline:<absolute-settings-path>`) so multiple installs on one host coexist. There is no staging directory, no symlink flip, no `releases/<tag>/` history on disk — pip's own install atomicity replaces the previous on-disk-rollback scheme.

The publish workflow (`.github/workflows/publish.yml`) is modelled on [pycastle's](https://github.com/Johannes-Kutsch/pycastle/blob/main/.github/workflows/publish.yml). Push to `main` → test → build → publish to **TestPyPI** via OIDC trusted publishing. Push a `v*.*.*` tag → test → build → publish to **PyPI** via OIDC. Version computed by setuptools-scm; tag builds error out if the computed version contains `.dev` (i.e. the tag must be on a clean commit). PRs run tests but do not build or publish.

## Why

- **PyPI is the ecosystem default for Python CLIs.** A one-line `pip install --upgrade` on the host replaces the prior `git fetch --tags` + clone + venv + smoke-test + symlink-flip choreography. The shell wrapper shrinks from ~150 lines to ~30.
- **pip's install atomicity is sufficient.** pip builds into a temp area and only swaps the installed package on success; a broken upgrade leaves the prior version in place. We give up the property "the previous release is sitting on disk and rollback is `ln -sfn`" in exchange for one-command rollback by version pin (`pip install application-pipeline==<older>`). Acceptable for a single-user pipeline.
- **Pycastle's `.sh` scripts are battle-tested.** Copying their shape (per-repo `.venv/`, marker-tagged crontab line, double-pip-upgrade for CDN propagation, end-of-tick log trim, global flock at `$XDG_CONFIG_HOME/<name>/.cron.lock`) inherits months of fixes for free.
- **TestPyPI on every `main` push** catches packaging-only regressions (missing `package-data`, malformed wheel metadata) before they reach a real tag, with zero cost when nothing is broken.
- **Trusted publishing via OIDC** keeps PyPI API tokens out of repo secrets. PyPI/TestPyPI project setup is a one-time hand operation (create project + register trusted publisher pointing at this repo + workflow + environment); thereafter every release is keyless.

## Considered alternatives

- **pipx install** instead of project-local `.venv/`. Rejected: the shell scripts copied from pycastle assume `.venv/bin/python` at a known relative path. Diverging from that pattern doubles maintenance for an aesthetic win.
- **Keep ADR-0009's staging+symlink shape on top of PyPI** (`pip install application-pipeline==<latest> --target releases/<version>/.venv/`, then flip symlink). Rejected: duplicates pip's own install atomicity, and on-disk rollback is rarely needed in practice for a single-user pipeline.
- **API-token publishing.** Rejected: tokens are a credential to leak.

## Consequences

- **Retires the previous git-tag pull model.** The Pi no longer has a `~/application-pipeline/{repo,releases,current}/` tree.
- **Legacy deploy artifacts are removed.** The seeded `setup/cron-install.sh` writes the crontab line; `docs/cron-setup.md` documents unattended operation host-agnostically; `docs/usage.md` documents install/CLI/configuration.
- **The package's `pyproject.toml` `package-data`** extends to include `templates/latex/*.{tex,cls,sty}`, `templates/user-info/*`, and `templates/setup/*.sh` so the seed files travel with the wheel.
- **`init_cmd._EXCLUDE_DIRS`** drops `"latex"` so `init` seeds the LaTeX template and moderncv class files alongside `config.py` / `layout.py` / `user-info/`. `"prompts"` stays excluded (prompts are hardcoded per ADR-0016).
- **`init` gains an `--refresh` mode** so `cron.sh` can self-heal new template files added in a release without manual intervention. Default behaviour (skip-existing) is unchanged.
- **Failure-report shape inside `cron.sh`** mirrors `src/application_pipeline/failure_report.py` but writes via a bash heredoc, because the venv may be in an inconsistent state during a failed upgrade. Stage labels distinguish deploy-stage (`ShellError`) from pipeline-stage failures.
- **pip-upgrade failures warn and continue.** A non-zero exit from either `pip install --upgrade` attempt captures pip's stderr and emits a single `WARNING:` line to stdout; no `ShellError` Failure Report is written and the tick proceeds to `init --refresh` and `run` on the currently-installed version. The warning lands in `application-pipeline/logs/cron.log` via the cron line's existing redirect, so a later Failure Report's embedded `tail -20 cron.log` surfaces it. Trade-off accepted: a broken release may persist for several days undetected; the operator notices via `cron.log` warnings or a version-pin spot-check (`pip show application-pipeline`). Resilience to transient PyPI / network hiccups beats fast detection of a stuck release for a single-user pipeline where rollback is one command (`pip install application-pipeline==<older>`).
- **Cron schedule is hardcoded at `30 0 * * *`** by `cron-install.sh` (per ADR-0024), chosen to land before pycastle's 01:00 tick on the shared host.
- **PyPI / TestPyPI project setup** is a one-time prerequisite documented in `docs/cron-setup.md`'s "Releasing" section — not a code contract.
