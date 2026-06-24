# Fuzzy dedup tier, in-memory pending, and cooldown decay

Three-part redesign of the **Deduplication Store**:

1. **Fuzzy tier** — token-subset matching (min 4 tokens, shorter ⊂ longer, gender markers stripped) as third tier after URL and exact-tuple. Alias written on hit, extending the tuple-match alias-write pattern.

2. **In-memory pending** — `is_seen()` writes `pending` entry to in-memory records and populates indexes on `miss`, without persisting. Closes race where two parser threads both get `miss` for the same tuple. Updated post-enrich when fields are backfilled.

3. **Cooldown decay** — `selected_by_judge` and `expired` suppress for `DEDUP_COOLDOWN_DAYS` (default 30), then decay: `selected_by_judge` → `judge_pending` (re-enters Pool), `expired` → `miss` (re-enters pipeline). Measured from `status_last_changed` (renamed from `first_seen`; silent migration on load).

`is_seen` returns `RunScopedSeenResult` carrying `listing_id: int` — `url_hit`/`tuple_hit`/`fuzzy_hit`/`judge_pending`/`run_hit`/`miss`. Tuple/fuzzy hits on `matched` entries return `judge_pending` (first in run, updates URL/title) or `run_hit`. Checked at two points: post-discover and post-enrich (backfilled fields). Freshness-dropped listings call `mark_expired(stub)` to populate indexes.

## Why

- **Fuzzy:** exact tuple misses syndicated copies with minor title variants. Token-subset false-positive rate decreases with title length (more tokens = harder subset match).
- **Pending:** concurrent parser threads without in-memory claim both classify same-tuple listings.
- **Cooldown:** without decay, `selected_by_judge` permanently suppresses still-live listings.

## Consequences

- `status_last_changed` replaces `first_seen`. Config knob `DEDUP_COOLDOWN_DAYS: int = 30`.
- `mark_expired` callable when no prior record exists.
