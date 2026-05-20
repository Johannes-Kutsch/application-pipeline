# Quota handling: parse Anthropic's stated reset time and sleep through it

When the Claude CLI returns `api_error_status: 429`, the pipeline parses the human-readable reset time from the response's `result` text, sleeps until `reset_time + 2 minutes`, and retries. If the reset time cannot be parsed, fallback is **next top-of-hour + 2 minutes**. No retry budget cap — the pipeline sleeps as long as the API tells it to. On wake, held LLM calls retry; a second quota hit fires the same loop.

This supersedes any prior degraded-success exit on usage limit. Companions: ADR-0020, ADR-0021, ADR-0022, ADR-0024.

## Why

- **Degrading to no-ops loses data on the day the user most wants output.** Under the once-per-day daily-file model (ADR-0024), a quota hit silently skipping the day is much worse than delaying the file a few hours. "Skip today silently" leaves the applicant with no signal.
- **Anthropic's 429 payload already carries the reset time.** The envelope's `result` text says `"...usage limit resets May 20, 3pm (UTC)"` (or `"...resets 3pm (UTC)"` same-day). The local `pycastle/` plugin already parses this format and is directly portable — same wire format, same `+2min` buffer.
- **The 2-minute buffer is operationally validated.** Anthropic's quota counters take a beat to open after the stated time; shorter risks the immediate retry hitting the same 429.
- **No retry budget cap is the right v1 trade-off.** A cap would mean giving up while the quota was still rolling shut. Applicant preference: "delayed file" over "no file with explanation". If outages routinely exceed 24h, cron-overlap (the sleeping run blocks the next cron via `flock -n`) will surface that operationally — revisit then.
- **Single-account is sufficient for v1.** Pycastle's multi-credential pool degenerates to one entry here; adding more later is a config change, not a refactor.

## Considered alternatives

- **Keep degraded-success exit.** Rejected: under one-fire-per-day a degraded run leaves nothing.
- **Cap retry budget at 6h, Failure Report on cap.** Rejected: arbitrary; the cron-overlap signal already surfaces extreme outages.
- **Fixed-interval sleep regardless of stated reset.** Rejected: churns on Anthropic's actual cycle.
- **Fail-fast; let cron retry tomorrow.** Rejected: cron is once-per-day; 24h skip.
- **Multi-account pool from day one.** Rejected: the pool surface only earns its keep if the operator has multiple subscriptions.

## Consequences

- **New module `src/application_pipeline/llm/quota.py`** holds the 429 `result`-text parser (regex + month table, ported from `pycastle/services/claude_service.py:88-145`). `parse_reset_time(result_text: str) -> datetime | None`.
- **`ClaudeUsageLimitError` carries `reset_time: datetime | None`.** The `LLMExtractor` raises it from `classify_relevance_batch` and `judge_top_n` on 429.
- **Sleep at the orchestrator level**, not inside `ClaudeExtractor`. Orchestrator catches `ClaudeUsageLimitError`, computes `wake_time = (reset_time or next_top_of_hour(now)) + timedelta(minutes=2)`, calls `time.sleep((wake_time - now).total_seconds())`, re-issues the failed call.
- **Classify batch retry**: holds the batch's items unmarked, sleeps, retries the same batch.
- **Judge retry**: re-issues with the same `JudgeCandidate` list. The pool is stable during sleep.
- **Non-quota LLM errors unchanged**: classifier batch errors fail that batch and leave items unmarked; judge errors → Failure Report and no daily file.
- **`degraded_reason` field is removed entirely.** Runs either complete or fail.
- **Cron-anchored logical day** (ADR-0021) handles midnight crossings: a 02:00 run that sleeps until 03:00 the next day still writes to the *original* day's file.
- **Cron overlap accepted.** `flock -n` silently skips an overlapping cron tick. "Yesterday missing, today present" is an acceptable signal.
- **Sleep duration logged** as `event=quota_sleep, reset_time=..., wake_time=..., duration_s=...` to `pipeline_orchestrator.events.jsonl`.
- **Multi-account pool deferred.**
