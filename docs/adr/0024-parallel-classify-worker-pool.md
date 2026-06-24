# Classifier runs a parallel worker pool

N classify calls run concurrently. `CLASSIFY_PARALLELISM` (default 4, `≥ 1`). Single accumulator thread fills batches; workers receive pre-built batches only (ADR-0038).

## Quota Wall

Shared coordination: `raise_wall(reset_time)`, `wait_if_blocked()`, `is_active()`. `threading.Condition` + `reset_time`. First worker to observe 429 publishes; remaining workers block at next iteration. One `event=quota_sleep` row per wall raise.

## Why

- Multi-hour serial runs at current volume. N concurrent calls compose cleanly with prompt cache. Fixed N — startup ramp not worth complexity.

## Consequences

- N `_ClassifyThread` instances. `CLASSIFY_PARALLELISM = 1` recovers single-worker behaviour.
- `RunMetrics` classify counters need lock (single-writer assumption broken).
