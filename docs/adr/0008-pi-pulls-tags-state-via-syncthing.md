# Pi pulls code via tags; state backs up via Syncthing

The Pi is a pure consumer of code, pure producer of state. Code reaches the Pi by polling the public GitHub repo for new `v*` tags (no credentials, no inbound network path). State (`data/results/*.md`, `.seen.json`, `data/failures/`, `data/logs/`) flows out via Syncthing. Nothing is pushed from the Pi to GitHub.

## Why

- **No credentials on the Pi.** Unattended device also hosts unrelated agentic-coding work; we want neither deploy key nor PAT. Pull-only code, Syncthing state.
- **Tag-gated releases match how the user works.** Agentic passes that land WIP on `main` do not deploy; only `git tag v1.1.x && git push --tags` does.
- **The "pipeline doesn't push" invariant is load-bearing.** ADR-0010 (failure reports via Syncthing files) and ADR-0002 (`.seen.json` via Syncthing) both depend on it.

## Consequences

- Pi cron wrapper does `git fetch --tags`, picks the highest `v*` tag, and switches via the staging-clone + symlink flip (ADR-0009) before invoking the package.
- Pi setup runbook clones over HTTPS (public). No SSH host key dance, no deploy-key generation.
- Syncthing folder is send-receive; laptop is read-only consumer by convention.
