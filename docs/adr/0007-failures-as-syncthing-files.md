# Failures surface as files in the settings folder

Run failures reported by writing markdown files to `<settings-dir>/.runtime-data/failures/<timestamp>.md` (ADR-0037): deploy errors (ADR-0020's `cron.sh`), orchestrator runtime errors, **Match Judge** failure, and fatal non-quota **LLM Extractor** backend/provider invocation failures. Quota errors do NOT trigger a Failure Report — they sleep per ADR-0016. Per-component event logs live under the convention from ADR-0012.

## Why

- No outbound credential on the host — the settings folder is already trusted and replicated.
- Same reset gesture as daily files: delete the failure file to acknowledge.
- Dated one-off files give natural pagination. No state machine.

## Consequences

- Markdown format with stage, error, and last 20 log lines.
- Stage names the failing path. Fatal classify/provider failures record an explicit LLM/classify stage, not ambient parser lifecycle context.
- No retention pruning beyond manual deletion.
