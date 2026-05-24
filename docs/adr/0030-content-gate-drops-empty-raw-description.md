# Content Gate drops empty `raw_description` post-enrich

Drops candidates whose body is empty after strip. Called by the parser thread post-enrich (ADR-0042). Dropped URLs are **not** marked in `seen.json` — re-discovered and re-checked next run, giving the source a chance to publish the body later.

## Why

- Empty body is a Position-level fact, not a parser-level one. Spreading defensive checks across parsers is the wrong shape.
- Classifier cost on empty body is deterministic-zero-value — model hallucinates or refuses.
- Named for the bucket ("Content Gate"), not the rule ("EmptyBodyGate") — leaves room for future body-validity rules.

## Consequences

- `ContentGate.admit(stripped_body, stub) -> bool` / `emit_run_complete()`.
- Log component `pipeline_content`. Reason enum `{passed, empty_body}`.
- No new dedup status — drop is transient.
- Effective customer post-ADR-0040 is non-native-enrich parsers; native paths either return non-empty or skip with a Failure Report.
