# Failures surface as files in the synced `data/` folder

> **Amended by [ADR-0022](0022-output-paths-anchored-to-data-dir.md):** the on-disk folder name is `data/`, not `synched/`. Paths below read `data/failures/<timestamp>.md` and `data/logs/<component>.log`.

Run failures and per-component failure-event streams are reported by writing files into the synced `data/` folder on the Pi. Two artifact shapes share the same transport:

1. **One-off failure reports** at `data/failures/<timestamp>.md` — written for fatal events that abort a run (deploy install errors, Ollama unreachable, parser crashes that exhaust retries, classify/judge exceptions, results-file write errors).
2. **Per-component append-only event logs** at `data/logs/<component>.log` — one file per configured parser plus `language.log`, written across runs with a `SUMMARY OF SESSION` trailer per session. Healthy sessions add only the trailer; sessions with attention-worthy events (`enrich_failed`, `external_redirect`, `unparseable_date`, `parser_dead`, language anomalies) add one event line per incident.

Syncthing propagates both shapes to the laptop. The pipeline never makes outbound calls (GitHub Issues, email, webhook) to notify of failure.

## Why

- **Preserves the PRD's "pipeline doesn't push" invariant.** PRD #15 explicitly excludes outbound notification ("the applicant pulls; the pipeline doesn't push"). Failure reporting via the existing Syncthing transport keeps that invariant intact while solving the silent-failure problem (Pi runs every 4h, fails silently, applicant only notices the stale timestamp).
- **No new credential on the Pi.** GitHub Issues would require a `Issues: write` PAT on an unattended device. The Syncthing folder is already trusted and replicated.
- **Same reset gesture as `current.md`.** For one-off reports, the applicant deletes the failure file to acknowledge it; for append-only event logs, an empty session body between two `SUMMARY OF SESSION` trailers is the "healthy session" signal — no acknowledgement needed. The mental model is uniform across artifacts.
- **History is preserved per-incident or per-session.** Dated one-off files give natural pagination for fatal incidents; append-only event logs give per-component forensic context across sessions ("did `stellen_hamburg` do its job in the last five runs?") without log spelunking.
- **Two shapes for two questions.** "Did the run abort?" is best answered by a new file appearing in `failures/`. "Did parser X do its job this run?" is best answered by scrolling its append-only log. Forcing both into one shape made each worse.

## Considered alternatives

- **Open a GitHub issue on failure** — rejected: violates the no-outbound-push principle and requires a credential on the Pi.
- **Single rolling `FAILURES.md` (append-only, reset by deleting)** — rejected for one-off reports: can't resolve individual incidents without resetting all of them.
- **Status footer in `current.md` only (no separate failure artifact)** — rejected: a failure that prevents the run from completing also prevents the footer from being written; the failure signal must live outside the pipeline's normal output path.
- **Log files outside the synced folder** — rejected: log files that don't sync force the applicant to SSH into the Pi, defeating the unattended-deployment goal.
- **One-off failure file per non-fatal event** (e.g. one `failures/external_redirect_<ts>.md` per redirect) — rejected: non-fatal events recur often enough that per-incident files would clutter the folder; per-component append-only logs are the right granularity for attention-worthy-but-not-aborting events.
- **Dedicated `prefilter.log` / `dedup.log`** — rejected: drops there are working-as-designed; per-line files would spam. Aggregate counters in the end-of-run `run complete:` line are the right granularity.

## Consequences

- **`data/failures/`** holds one-off fatal-incident reports. Pi writes; laptop receives.
- **`data/logs/`** holds per-component append-only event logs. One file per parser configured in the source list, plus `language.log`. Pi writes; laptop receives.
- **Failure file format** (markdown, per-incident, one-off):
  ```
  # Run failed at 2026-05-11 16:04 (tag v1.1.0)

  **Stage:** parser:bundesagentur
  **Error:** httpx.ConnectError: ...
  **Last 20 log lines:**
  ...
  ```
- **Per-component event log format** (plain text, append-only, grep-friendly):
  ```
  2026-05-12T15:30:00Z parser started
  2026-05-12T15:30:42Z enrich_failed stub_url=https://... reason=...
  2026-05-12T15:31:07Z external_redirect stub_url=https://... outbound=https://...

  SUMMARY OF SESSION 2026-05-12T15:30:00Z
  discovered=12 enrich_failed=1 external_redirects=1 duration=47.3s


  2026-05-12T16:00:00Z parser started
  ...
  ```
  ISO-8601 timestamp prefix; `key=value` pair body; `SUMMARY OF SESSION` trailer per session separated by a blank line from the next session. An empty body between two trailers means the parser worked. The file format is owned by a single module so future evolution (extra fields, JSONL migration) touches one place.
- **Trigger surfaces:**
  - The cron wrapper writes one-off failure files for deploy-stage errors (clone, install, smoke-test).
  - The orchestrator writes one-off failure files for run-stage fatal errors (Ollama unreachable, parser exhausted retries, results-file write error) AND is the sole writer of per-component event logs (parser threads stay pure producers per ADR-0007; events flow through the outbound queue and `Position._warnings`).
- **Acknowledgement gestures:**
  - One-off failure report: applicant deletes the file (Syncthing propagates the delete; Pi-side cleanup is automatic). No state machine.
  - Per-component event log: no acknowledgement needed; the empty-body-between-trailers signal is self-evident. Files grow unbounded across runs; rotation policy is deferred until an actual problem motivates it (healthy runs add only the trailer line, so growth is bounded by failure frequency in practice).
- **Failure files are never written to `.seen.json`** — the failure subject is the *run*, not a position. Position-level errors that leave a position un-evaluated are recorded via `status: "enrich_failed"` in `.seen.json` as specified in CONTEXT.md, and as an event line in the responsible parser's event log.
- **No retention pruning** for one-off failure files beyond what the applicant does manually. A persistently-broken pipeline accumulates one failure file per run (every 4h, ~6/day) — visible enough that "fix it or pause the cron" is the obvious response.
