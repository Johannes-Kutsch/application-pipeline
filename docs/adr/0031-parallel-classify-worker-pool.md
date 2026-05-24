# Classifier runs a parallel worker pool of fixed size N

N solo `claude -p` calls concurrently from a fixed pool. `Config.claude_classify_parallelism` (default 4, `≥ 1`). Solo-call protocol (ADR-0028), stdin-combined prompt (ADR-0029), and DeduplicationStore lock all preserved. Supersedes ADR-0028's explicit rejection of N>1.

Amended by ADR-0042: workers now drain a classify queue and run only the LLM call, no body fetch.

## Why

- ADR-0028's "17-min serial wall-clock" premise no longer holds at current volume — multi-hour runs.
- N concurrent solo calls compose cleanly with the prompt cache (first N calls miss once; amortised over thousands).
- Fixed N, not ramp — startup machinery to save N cold-miss prefixes not worth complexity.

## Quota Wall

Shared coordination object: `raise_wall(reset_time) -> bool`, `wait_if_blocked()`, `is_active()`. Implemented as `threading.Condition` + `reset_time` field. First worker to observe 429 publishes; remaining workers block at next iteration. One `event=quota_sleep` row per wall raise, not per worker. In-flight calls at wall-raise time finish — they re-queue and sleep-block next dequeue.

## Consequences

- N `_ClassifyThread` instances on shared queue. `_NO_MORE_BATCHES` sentinel enqueued N times.
- `claude_classify_parallelism = 1` recovers single-worker behaviour exactly.
- `RunMetrics` classify counters need lock or atomic adds (single-writer assumption broken).
