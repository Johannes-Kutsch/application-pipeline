# Match Tier retired; judge picks top-N from the in-domain pool

The **Match Judge** no longer assigns a `tier ∈ {green, amber, red}` per **Position**. Instead, the judge takes the entire **Pool** for today and returns the top **N=5** ranked against the **Triage Profile** and **Skills**, with each winner carrying its **Match Verdict** (`matched`, `missing`, `summary`) plus an explicit `rank: 1..N`. The **Match Tier** concept is gone — from `MatchVerdict`, from the **Layout**'s placeholder vocabulary, from the **Daily Results File**, and from operator metrics.

Companion ADRs: ADR-0021 (daily file), ADR-0022 (extracts/pool), ADR-0023 (quota), ADR-0024 (once-per-day cron).

## Why

- **Per-item judging is the dominant token cost.** Today every in-domain item drives one `judge_match` call carrying the full `raw_description`; the same description is judged again next run if re-discovered. The pool concept + single top-N call collapses N per-item calls into 1, and per-item description tokens stop being paid on rediscovery.
- **Tier was coarse ranking with no comparative signal.** Ten "green" listings had no ordering. The applicant reads green top-down already — explicit rank gives them what they were doing implicitly. The amber/red files were dead surface.
- **Drops three pieces of dead complexity.** Trio collapses to one daily file (ADR-0021); `emoji`/`color`/`tier` placeholders stop being meaningful; Renderer's placeholder dict shrinks.
- **Bounded by pool, not inflow.** One judge call per run, regardless of pool size, with compact structured extracts (ADR-0022) per candidate.

## Considered alternatives

- **Keep tier alongside rank.** Rejected: tier carries no information not in rank.
- **Top-N as a `Config` field.** Rejected: N is a code constant (5, "what the applicant reads over morning coffee"); reconsider if usage proves otherwise.
- **Two-stage judge (rank pass + per-winner detail pass on full descriptions).** Rejected: extracts carry enough signal for `matched`/`missing`/`summary`; second pass re-pays description tokens for no observable fidelity gain.
- **Tier-based file split kept; judge picks top-N per tier.** Rejected: re-introduces the trio's surface for a ranking model that no longer needs it.

## Consequences

- `MatchVerdict` drops `tier`, adds `rank: int` (validated `1 ≤ rank ≤ N`). `MatchTier` enum removed.
- `LLMExtractor.judge_match` signature changes to `judge_top_n(candidates: list[JudgeCandidate]) -> list[MatchVerdict]` where each candidate carries the stable id + structured extract (ADR-0022) and the returned list has length ≤ N.
- Judge prompt template moves from "score one listing" to "select top 5, output `<verdicts>[...]</verdicts>` with one entry per winner including `id, rank, matched, missing, summary`".
- **Renderer** drops `emoji`/`color`/`tier`. `CARD_TEMPLATE` gains `{rank}` (`**Rank 1/5**`-style). Existing user `layout.py` referencing removed placeholders raises `LayoutError` on load — accepted: single-shot user migration.
- **Card** structure: H1 unchanged; meta line unchanged; `## AI Assessment` opens with `**Rank {n}/5**`, then summary, then matched/missing; `## Job Description` unchanged.
- `.seen.json` `status` renamings: `kept` → `selected_by_judge`; `off_domain` → `out_of_domain`; `classified_in_domain` → `in_domain` (semantics shift per ADR-0022). `enrich_failed` and `external_redirect` unchanged. No automatic migration — see ADR-0024.
