# Fuzzy dedup tier, in-memory pending, and cooldown decay

Three-part redesign of the **Deduplication Store**:

1. **Fuzzy tier** — token-subset matching (min 4 tokens in shorter title, shorter ⊂ longer, gender markers stripped) as a third tier after URL and exact-tuple. Separate index keyed by `(company_lc, location_lc)` → `list[(token_set, url)]`. Writes alias on hit, same as exact-tuple (ADR-0003).

2. **In-memory pending** — `is_seen()` writes a `pending` entry to in-memory `_records` and populates tuple + fuzzy indexes on `miss`, without persisting. Closes the race where two parser threads both get `miss` for the same tuple because `_mark()` hasn't run yet. Classify worker overwrites with real status and persists. Updated post-enrich when fields are backfilled.

3. **Cooldown decay** — `selected_by_judge` and `expired` entries suppress for `DEDUP_COOLDOWN_DAYS` (config knob, default 30), then decay: `selected_by_judge` → `judge_pending` (re-enters Pool), `expired` → `miss` (re-enters pipeline). Measured from `status_last_changed` (renamed from `first_seen`; silent migration on load).

Additional changes:
- `is_seen` returns new `fuzzy_hit` variant (widens `SeenResult`).
- Tuple/fuzzy hits on `matched` entries return `judge_pending` (first in run, updates URL/title with fresh data) or `run_hit` (subsequent). Fixes existing bug where tuple-matched `matched` entries didn't enter the Pool.
- Dedup checked at two points: post-discover and post-enrich (backfilled fields).
- Freshness-dropped listings: orchestrator calls `mark_expired(stub)` so tuple/fuzzy index knows about them. `status_last_changed` refreshed on each re-drop.
- Per-hit JSONL logging to `pipeline/dedup.events.jsonl`.

## Why

- **Fuzzy tier:** Exact tuple misses syndicated copies with minor title variants (extra qualifier word, regional suffix). Token-subset chosen over edit-distance because false-positive rate decreases with title length (more tokens = harder subset match), while edit-distance false-positives increase with length (boilerplate dilutes meaningful differences).
- **Pending:** Parser threads run concurrently across sources. Without in-memory claim, same-tuple listings from different sources both classify — wasting LLM tokens.
- **Cooldown:** Without decay, `selected_by_judge` permanently suppresses a still-live listing the user might want to reconsider; `expired` permanently blocks a dateless repost that could be fresh.
- **Freshness → store write:** Without it, a freshness-dropped listing never enters the tuple index, so its dateless twin (same tuple, no date) passes dedup and wastes an LLM call.

## Consequences

- `SeenResult` gains `fuzzy_hit`. Callers treat it like `tuple_hit` (skip) unless status triggers special handling.
- `first_seen` field renamed to `status_last_changed` across store. Load-time migration: presence of `first_seen` without `status_last_changed` triggers silent rename.
- `mark_expired` callable when no prior record exists (freshness-dropped listing never seen before).
- New config knob `DEDUP_COOLDOWN_DAYS: int = 30`.
- Amends ADR-0003 (alias also written on fuzzy hit), ADR-0005 (new variant).
