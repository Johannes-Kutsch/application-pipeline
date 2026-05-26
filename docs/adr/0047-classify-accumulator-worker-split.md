# Single accumulator thread fills classify batches; workers dispatch only

Batch accumulation moves from N parallel workers to a single `_ClassifyAccumulator` thread. The accumulator pulls items from the classify queue, fills batches of `claude_classify_batch_size` sequentially, and puts complete batches onto a dispatch queue. N `_ClassifyWorker` threads consume pre-built batches and run only the LLM call + verdict handling. `claude_classify_parallelism` still sizes the worker pool.

## Why

With N workers each accumulating independently on a shared item queue, OS thread scheduling scattered items across workers. A run producing 10 items with `batch_size=10` and 4 workers would split into e.g. 7+3 across two workers — two LLM calls instead of one. The accumulator guarantees one batch fills completely before the next starts, while LLM calls still run in parallel.

## Considered

- **Mutex on the queue loop** (worker holds lock while accumulating, releases on batch-full). Simpler diff, but mixes batching and LLM concerns in one thread and the lock interaction with `_process_batch` blocking needs care.
- **Time-based flush** on the accumulator (submit partial batch after N seconds). Rejected — classify is terminal in the pipeline, nothing downstream is latency-sensitive, and it adds complexity for no real benefit.

## Consequences

- Accumulator receives one `_NO_MORE_BATCHES` sentinel, flushes its partial batch, then sends N sentinels to workers.
- `is_degraded` check stays in workers, not accumulator — accumulator is a dumb batcher.
- Accumulator does not poll for `run_state.is_aborted` — waits for natural sentinel arrival.
- `claude_classify_parallelism = 1` still recovers single-worker behaviour.
