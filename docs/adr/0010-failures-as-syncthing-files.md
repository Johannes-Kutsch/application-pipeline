# Failures surface as files in the synced `data/` folder

Run failures are reported by writing markdown files into `data/failures/<timestamp>.md` on the Pi: deploy install errors, orchestrator runtime errors, **Match Judge** call failure (no daily file), and non-quota classifier errors that left the run with no writeable output. Quota errors do NOT trigger a Failure Report — they sleep until reset per ADR-0023. Per-component append-only event logs live in `data/logs/` under the convention defined by ADR-0018. The pipeline never makes outbound calls.

## Why

- **Preserves "pipeline doesn't push" (ADR-0008).** GitHub Issues would require a PAT on an unattended device. The Syncthing folder is already trusted and replicated.
- **No new credential on the Pi.**
- **Same reset gesture as the daily files.** Applicant deletes the failure file to acknowledge; Syncthing propagates the delete.
- **Dated one-off files give natural pagination.** No state machine, no per-incident resolution dance.

## Consequences

- **Failure file format** (markdown, per-incident):
  ```
  # Run failed at 2026-05-11 16:04 (tag v1.1.0)

  **Stage:** parser:bundesagentur
  **Error:** httpx.ConnectError: ...
  **Last 20 log lines:** ...
  ```
- **Trigger surfaces:** cron wrapper for deploy-stage errors; orchestrator for run-stage fatal errors. Per-component event logs are written via the structured logger from ADR-0018, not by writers writing to `failures/`.
- **Acknowledgement:** delete the file. No state machine.
- **No retention pruning** for one-off failure files beyond what the applicant does manually.
- **Position-level errors** ride `.seen.json` `status` values (`enrich_failed`, `external_redirect`, `expired`) instead.
