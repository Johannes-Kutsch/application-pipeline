# Failures surface as files in the synced `synched/` folder

Run failures (deploy install errors, Ollama unreachable, parser crashes, classify/judge exceptions) are reported by writing a markdown file to `results/failures/<timestamp>.md` on the Pi. Syncthing propagates the file to the laptop. The pipeline never makes outbound calls (GitHub Issues, email, webhook) to notify of failure.

## Why

- **Preserves the PRD's "pipeline doesn't push" invariant.** PRD #15 explicitly excludes outbound notification ("the applicant pulls; the pipeline doesn't push"). Failure reporting via the existing Syncthing transport keeps that invariant intact while solving the silent-failure problem (Pi runs every 4h, fails silently, applicant only notices the stale timestamp).
- **No new credential on the Pi.** GitHub Issues would require a `Issues: write` PAT on an unattended device. The Syncthing folder is already trusted and replicated.
- **Same reset gesture as `current.md`.** The applicant deletes (or moves) the failure file to acknowledge it; the next failure of the same shape creates a new dated file. The mental model is uniform across artifacts.
- **History is preserved per-incident.** Dated files give natural pagination — "this has been failing for three days" is visible in the file listing without log spelunking.

## Considered alternatives

- **Open a GitHub issue on failure** — rejected: violates the no-outbound-push principle and requires a credential on the Pi.
- **Single rolling `FAILURES.md` (append-only, reset by deleting)** — rejected: can't resolve individual incidents without resetting all of them; per-file resolution matches how the applicant actually triages.
- **Status footer in `current.md` only (no separate failure artifact)** — rejected: a failure that prevents the run from completing also prevents the footer from being written; the failure signal must live outside the pipeline's normal output path.
- **Log file only, no Syncthing surface** — rejected: log files don't sync; applicant has to SSH into the Pi to see them, defeating the unattended-deployment goal.

## Consequences

- **`synched/failures/`** is a subfolder of the Syncthing-synced `synched/` folder. Pi writes to it; laptop receives.
- **Failure file format** (markdown, per-incident):
  ```
  # Run failed at 2026-05-11 16:04 (tag v1.1.0)

  **Stage:** parser:bundesagentur
  **Error:** httpx.ConnectError: ...
  **Last 20 log lines:**
  ...
  ```
- **Failure trigger surface:** the cron wrapper writes failure files for deploy-stage errors (clone, install, smoke-test); the orchestrator writes failure files for run-stage errors (Ollama unreachable, parser exhausted retries, results-file write error).
- **Acknowledgement gesture:** applicant deletes the file (Syncthing propagates the delete; Pi-side cleanup is automatic). No state machine.
- **Failure files are never written to `.seen.json`** — the failure subject is the *run*, not a position. Position-level errors that leave a position un-evaluated are recorded via `status: "enrich_failed"` in `.seen.json` as specified in CONTEXT.md.
- **No retention pruning** beyond what the applicant does manually. A persistently-broken pipeline accumulates one failure file per run (every 4h, ~6/day) — visible enough that "fix it or pause the cron" is the obvious response.
