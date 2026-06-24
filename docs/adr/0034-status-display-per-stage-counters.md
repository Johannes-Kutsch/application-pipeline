# Status display: uniform per-stage counters

Every stage reports three uniform counters — **queued, dropped, forwarded**. Each parser gets two rows: parser row (`K discovered · [enrich_failed if nonzero] · N forwarded`) and gates row (non-zero drop counters only). `llm_classify_relevance` row adds `malformed` + `classifying` counters; `queued` shows current depth. Judge: terminal message only.

## Why

- Uniform counter shape across all stages. Zero-drop counters hidden to reduce noise.

## Consequences

- Per-gate rows and `llm_judge_match` row retire. Freshness drops from both arms summed into one counter.
