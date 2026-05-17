# Pi pulls code via tags; `.seen.json` backs up via Syncthing

The Pi 5 is a pure consumer of code and a pure producer of state. Code reaches the Pi by polling the public GitHub repo for new `v*` tags (no credentials, no inbound network path). State (`current.md`, `.seen.json`, `results/failures/`) flows out via Syncthing. Nothing is pushed from the Pi to GitHub.

## Why

- **No credentials on the Pi.** The Pi runs unattended and also hosts unrelated agentic-coding work; we want neither a deploy key nor a PAT on it. Pull-only on code and Syncthing on state achieves that.
- **Tag-gated releases match how the user actually works.** The Pi pulls a deliberate version, not "whatever is on `main`". An agentic coding pass that lands experimental code on `main` does not deploy to the Pi; only `git tag v1.1.x && git push --tags` does.
- **The "pipeline doesn't push" invariant is load-bearing.** PRD #15 declares this explicitly; ADR-0012 (failure reporting via Syncthing files) and ADR-0002 (`.seen.json` durability via Syncthing) both depend on it.

## Considered alternatives

- **`make pi-deploy` from the laptop pushes code over rsync** — rejected: couples deploy to the laptop being on, and depends on the laptop's working tree rather than a tagged release. The Pi-polls-tags model decouples deploy from any other machine.
- **GitHub Actions SSHes into the Pi on tag push** — rejected: introduces inbound network surface (port forward or self-hosted runner) for a benefit (faster pickup than the next cron tick) that doesn't matter for a 4-hourly job pipeline.
- **Pi tracks `main` directly instead of tags** — rejected: agentic-coding pushes land WIP on `main` frequently; deploying every push would routinely strand the Pi on incomplete work.

## Consequences

- **Pi cron wrapper** does `git fetch --tags`, picks the highest `v*` tag, and switches to it (via the staging-clone + symlink flip pattern, per ADR-0011) before invoking `python -m application_pipeline`.
- **Pi setup runbook** clones the repo over HTTPS (public), so no SSH host key dance, no deploy-key generation.
- **Syncthing folder** carries `current.md` (per the **Results File** definition in CONTEXT.md), `.seen.json` (per ADR-0002), and `data/failures/` (per ADR-0012). Folder is send-receive; the laptop is a read-only consumer by convention.
