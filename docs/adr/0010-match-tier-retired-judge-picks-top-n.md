# Match Tier retired; judge picks top-N from pool

**Match Judge** takes the entire **Pool** and returns top N=5 ranked against **Triage Profile** and **Skills**. `rank: 1..N` replaces `tier ∈ {green, amber, red}`.

## Why

- Per-item judging was dominant token cost. Pool + single top-N call collapses N calls into 1.
- Explicit rank gives comparative signal the tier lacked.

## Consequences

- `MatchVerdict`: `{id: int, rank: int}` where `id` is **Listing ID**. `MatchTier` enum removed.
- `judge_top_n(candidates)` replaces per-item `judge_match`.
- Dedup status renames: `kept` → `selected_by_judge`; `off_domain` → `out_of_domain`.
