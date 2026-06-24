# Single accumulator thread fills classify batches; workers dispatch only

Batch accumulation moves from N parallel workers to a single `_ClassifyAccumulator` thread. Accumulator pulls items from classify queue, fills batches of `CLASSIFY_BATCH_SIZE` sequentially, puts complete batches onto dispatch queue. N `_ClassifyWorker` threads consume pre-built batches.

## Why

- With N workers each accumulating independently, OS scheduling scattered items across workers — e.g. 10 items split 7+3 across two workers instead of one batch. Accumulator guarantees one batch fills before next starts.

## Consequences

- Accumulator receives one sentinel, flushes partial batch, sends N sentinels to workers.
- `CLASSIFY_PARALLELISM = 1` recovers single-worker behaviour.
