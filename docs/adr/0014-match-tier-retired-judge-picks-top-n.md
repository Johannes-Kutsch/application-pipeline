# Match Tier retired; judge picks top-N from the in-domain pool

**Match Judge** no longer assigns `tier ∈ {green, amber, red}`. Takes the entire **Pool** and returns top N=5 ranked against **Triage Profile** and **Skills**, each carrying `rank: 1..N`. Companions: ADR-0015 (daily file), ADR-0016 (quota), ADR-0017 (once-per-day cron).

## Why

- Per-item judging was the dominant token cost. Pool + single top-N call collapses N per-item calls into 1.
- Tier was coarse ranking with no comparative signal. Explicit rank gives what the applicant was doing implicitly.
- Drops dead complexity: trio → one daily file, `emoji`/`color`/`tier` placeholders retire.

## Consequences

- `MatchVerdict` drops `tier`, adds `rank: int` (1 ≤ rank ≤ N). `MatchTier` enum removed.
- `judge_top_n(candidates: list[JudgeCandidate]) -> list[MatchVerdict]` replaces per-item `judge_match`.
- `.seen.json` status renamings: `kept` → `selected_by_judge`; `off_domain` → `out_of_domain`. No auto-migration (ADR-0017).
