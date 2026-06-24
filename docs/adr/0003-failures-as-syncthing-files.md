# Failures surface as files in the settings folder

Markdown at `<settings-dir>/.runtime-data/failures/<timestamp>.md` (ADR-0028): deploy errors (ADR-0015's `cron.sh`), orchestrator runtime errors, **Match Judge** failure, fatal non-quota **LLM Extractor** backend/provider invocation failures. Quota errors sleep (ADR-0012).

## Why

- No outbound credential on the host — settings folder already trusted and replicated. Same acknowledge-by-delete gesture as daily files.

## Consequences

- Markdown with stage, error, last 20 log lines. Fatal classify/provider failures record an explicit LLM/classify stage.
