# Classifier reintroduces small-batch calls; solo retired

Supersedes ADR-0028. `classify_relevance(items: list[ClassifyItem]) -> list[RelevanceVerdict | None]` takes up to `claude_classify_batch_size` items (default 10, configurable). Each item gets a numbered id in the prompt; each verdict is returned as an individually-tagged `<verdict id="N">{...}</verdict>` block. Verdicts that fail to parse are returned as `None` — the corresponding listings stay unmarked and are re-discovered next run. No retry.

Worker drains up to `batch_size` items from the classify queue, fires immediately when the batch is full or when the sentinel signals all parsers are done. Parallel worker pool (ADR-0031) unchanged at default 4.

## Why

- Subscription volume: solo calls multiply per-call overhead. At ~200 listings/day, batching 10 reduces calls from ~200 to ~20 — 10x fewer conversation turns billed against the subscription.
- ADR-0028's blast-radius concern is mitigated: batch of 10 loses at most 10 verdicts (not 100), and the per-tag output protocol means partial parse recovers the valid verdicts.
- Lost-in-the-middle: irrelevant at batch size 10 — total context stays well under the attention-degradation threshold.

## Consequences

- `Config.claude_classify_batch_size: int` reintroduced (default 10, `≥ 1`).
- Prompt switches from single `<verdict>` to multiple `<verdict id="N">` tags.
- `_ClassifyWorker._process` accumulates up to `batch_size` items before calling.
- Unparseable verdicts within a batch are silently dropped — listings re-enter next run via normal discovery.
- Event/transcript volume drops proportionally (~20 rows/day vs ~200).
