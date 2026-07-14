# Content Gate drops empty `raw_description` post-enrich

Drops candidates whose body is empty after strip. Called by parser thread post-enrich (ADR-0028). Dropped URLs not marked in `seen.json` — re-checked next run.

## Why

- Classifier cost on empty body is deterministic-zero-value. Named for the bucket, not the rule.

## Consequences

- `ContentGate.admit(stripped_body, stub) -> bool`. Reason enum `{passed, empty_body, too_short}`.
- No new dedup status — drop is transient.
- Effective customer post-ADR-0030: non-native-enrich parsers.
