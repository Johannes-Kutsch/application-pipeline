# Freshness Gate drops stale listings post-enrich

A new deterministic stage, the **Freshness Gate**, runs after `Parser.enrich()` and before the **Relevance Classifier**. It drops a **Position** when either `posted_date` is older than `Config.MAX_LISTING_AGE_DAYS` (default 180) or `deadline` is before the cron-anchored logical date. Drops write a new terminal-skip dedup status `expired` and, when the source was a pool member, delete the **Structured Extract** from `data/extracts.json`. The gate re-runs on every enrich including Pool re-discovery, so `in_domain → expired` transitions are possible.

## Why

- **The brief is freshness, not domain fit.** Conflating with `out_of_domain` muddies semantics — `out_of_domain` means "wrong professional fit, forever".
- **Two date fields, two failure modes, one gate.** `posted_date` age and `deadline < today` both invalidate a listing; neither alone is sufficient. Treating each `None` as "no signal" lets the gate act on whatever signal a source exposes.
- **Post-enrich is the only correct placement.** `posted_date`/`deadline` live on `Position`, not `PositionStub`.
- **A new status, not an overload.** Two terminal-skip statuses keep per-keyword analytics honest, surface the `expired` count separately, and leave room for a future "re-enable expired URLs on re-post" policy.
- **Gate re-runs on Pool re-discovery to bound staleness.** Cost is free (Position is enriched anyway to render the Card). A 175-day-old listing won't linger as the source keeps surfacing it.
- **Cron-anchored "today".** A run sleeping through quota (ADR-0023) keeps the same threshold reference it started with.
- **Deadline-passed is non-tunable.** Only `MAX_LISTING_AGE_DAYS` is configurable. Validation: `≥ 1`.

## Considered alternatives

- **Fold into the Domain Pre-Filter.** Rejected: prefilter is title-only on stubs (ADR-0019); date check needs post-enrich `Position` fields.
- **Reuse `out_of_domain`.** Rejected: collapses two reasons; breaks per-keyword analytics; forecloses future policy.
- **First-contact only (`not_classified → expired`).** Rejected: pool items would never expire if the source keeps surfacing.
- **Let the classifier handle staleness via the prompt.** Rejected: spends tokens on a check `today - posted_date` can do for free.
- **Drop the deadline check.** Rejected: misses fresh-posted listings with passed deadlines (real failure mode on bundesagentur).

## Consequences

- **`.seen.json` status enum gains `expired`.** Per ADR-0024 the deploy already wipes `.seen.json`. Going forward, an unknown status raises (no silent translation).
- **`DeduplicationStore` gains `mark_expired(stub)`.** When prior status was `in_domain`, the call also deletes the URL's entry from `data/extracts.json`.
- **`Config` gains `MAX_LISTING_AGE_DAYS: int` (default 180).** Validated `≥ 1`. Template ships the default with a comment.
- **New log component `pipeline_freshness`** (per ADR-0018). Per-Position transcripts: `url, title, source, posted_date, deadline, anchored_today, age_days, passes, reason ∈ {passed, too_old, deadline_passed, too_old_and_deadline_passed}`. Per-run `event=run_complete` aggregate with counts by reason. **Agent Row** between `pipeline_prefilter` and `llm_classify_relevance`.
- **Pipeline order**: `discover → dedup → prefilter (title) → enrich → freshness gate → classifier → judge → render`.
- **Pool shrinkage before judge.** Judge candidate count can be smaller than yesterday's pool size even when nothing new failed today.
- **Future-dated `posted_date` (parser data error) passes.** Negative age passes silently. Parser data hygiene is not this gate's job.
- **No new Failure Report mode.** Run-end metrics surface the count.
