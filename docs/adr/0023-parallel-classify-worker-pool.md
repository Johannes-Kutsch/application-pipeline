# Classify worker pool: parallel workers, accumulator thread, small batches

N classify calls run concurrently. `CLASSIFY_PARALLELISM` (default 4, `≥ 1`). Single `_ClassifyAccumulator` thread fills batches of `CLASSIFY_BATCH_SIZE` items (default 10) sequentially and puts complete batches onto dispatch queue. N `_ClassifyWorker` threads consume pre-built batches and run only the LLM call.

## Quota Wall

Shared coordination: `raise_wall(reset_time)`, `wait_if_blocked()`, `is_active()`. `threading.Condition` + `reset_time`. First worker to observe 429 publishes; remaining workers block at next iteration. One `event=quota_sleep` row per wall raise.

## Why

- Serial classify at ~200 listings/day causes multi-hour runs. N concurrent calls compose with prompt cache.
- Solo calls multiply per-call overhead. Batching 10 reduces calls 10× with limited blast radius (≤10 verdicts lost per batch).
- Single accumulator prevents OS scheduling from scattering items across workers (e.g. 10 items split 7+3 instead of one full batch).

## Consequences

- N `_ClassifyThread` instances. `CLASSIFY_PARALLELISM = 1` recovers single-worker behaviour.
- `Config.classify_batch_size: int` / `CLASSIFY_BATCH_SIZE` (default 10, `≥ 1`). Verdict per-item: `<verdict id="N">{...}</verdict>`. Unparseable verdicts → `None`, listing unmarked, retried next run.
- Accumulator receives one sentinel, flushes partial batch, sends N sentinels to workers.
- `RunMetrics` classify counters need lock (single-writer assumption broken by N workers).
