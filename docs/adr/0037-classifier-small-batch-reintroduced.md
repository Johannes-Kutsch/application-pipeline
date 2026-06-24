# Classifier reintroduces small-batch calls

`classify_relevance(items: list[ClassifyItem]) -> list[RelevanceVerdict | None]` takes up to `CLASSIFY_BATCH_SIZE` items (default 10). Each item numbered in prompt; each verdict as `<verdict id="N">{...}</verdict>`. Unparseable verdicts → `None`, listing unmarked, retried next run.

Single accumulator thread fills batches (ADR-0038); dispatch workers run LLM call only. Worker pool (ADR-0024) unchanged at default 4.

## Why

- Solo calls at ~200 listings/day multiply per-call overhead. Batching 10 reduces calls 10x. Blast-radius limited: batch of 10 loses at most 10 verdicts. Per-tag protocol enables partial parse recovery.

## Consequences

- `Config.classify_batch_size: int` / `CLASSIFY_BATCH_SIZE` (default 10, `≥ 1`).
- Event/transcript volume drops proportionally.
