# `classified_in_domain` dedup state lets the next run skip classify

The **Deduplication Store** gains a fifth per-URL status, `classified_in_domain`, written by the classify worker immediately after a successful `classify_relevance_batch` returns `in_domain=True` and *before* the stub is enqueued for the **Match Judge**. `is_seen` grows a fourth `SeenResult` variant (e.g. `judge_pending`) that the **Pipeline Orchestrator** routes through `enrich()` directly into the judge queue, bypassing the classify stage. On a successful judge call the existing `mark_kept` transition overwrites `classified_in_domain` → `kept`; the new state is transient by design and exists only for the window between "survived classify" and "survived judge."

## Why

- **Cross-run retry shouldn't double-pay classify tokens.** Per ADR-0016, the pipeline's response to a transient CLI failure is "raise → don't mark seen → next cron tick re-pays." That policy is the right shape for *enrich* (network only) but is asymmetric for *judge*, because every judge-stage failure currently re-pays the prior `classify_relevance` call on the next run too. On the failure cluster in `judge_match.log` 2026-05-16T18:13 (four listings to Anthropic 5xx in 11 seconds, plus one schema overrun), the existing policy means tomorrow's run silently re-pays five classify calls' worth of tokens for verdicts the classifier already produced.

- **The dedup store already records intermediate per-URL stage status.** `enrich_failed` and `external_redirect` are not "this listing is finished"; they are "this listing reached this stage and stopped." Adding `classified_in_domain` continues that pattern: it is a per-URL stage marker, not a verdict. The state lives next to its siblings in `.seen.json`, uses the same single-writer-per-process / `threading.Lock`-guarded write path, and survives crashes/syncing/restarts the same way the existing four statuses do.

- **No in-process retry on the CLI.** ADR-0016 explicitly rejected in-process retry of failed CLI calls ("operator reports retries have never resolved the failure mode in practice"). This ADR does not contradict that — the cross-run retry remains the only retry. What changes is that the cross-run retry now resumes from the post-classify stage boundary instead of restarting at the source. The pipeline becomes idempotent at the classify→judge boundary; longer outages (hours, days) and brief blips (the observed 11-second cluster) get the same treatment.

- **`enrich()` is re-paid, not persisted.** The new state stores no `raw_description` and no parsed `Position` — only the URL-level fact that classification was already done. The rerouted run re-fetches the listing page via `Parser.enrich(stub)`. The dedup store stays a state tracker, not a content cache; the "Deduplication" concept does not widen into "Stage Cache."

- **Operator visibility is preserved.** The **Run Divider** gains a conditional `judge_resumed=<n>` field (omitted when zero), counting stubs that took the new path. Same pattern as `dedup_url_hits` / `dedup_tuple_hits` per ADR-0008 — without the counter, a regression where the rerouting silently breaks goes unnoticed until the token bill is examined.

## Considered alternatives

- **In-call retry with exponential backoff on detected-transient CLI errors.** Rejected: ADR-0016 already considered and rejected in-process CLI retry on operational grounds, and it only protects against outages shorter than the retry budget. The token-waste concern is architectural (stage boundaries aren't idempotent), not transient (retries succeed). A retry layer leaves the underlying asymmetry between enrich-retry and classify-retry in place.

- **Do nothing; rely on the next cron tick.** Rejected for the stated reason — re-paying classify tokens on every transient judge failure is exactly the cost this change is meant to avoid. The existing policy is the right answer for stages whose cost is network-only.

- **Persist `raw_description` (or full `Position`) in the dedup store.** Rejected: turns the dedup store into a content cache, which is a separate concept that does not earn its keep for v1. `enrich()` is cheap (HTTP fetch against a source with its own pacing) and pays no tokens. If a future operational concern shifts toward source rate limits or fetch failures, a content cache is the right tool — but as a sibling concept, not by widening Deduplication.

- **Add a sibling "Stage Cache" concept distinct from Deduplication.** Rejected: a clean separation in theory, but the dedup store already carries intermediate states (`enrich_failed`, `external_redirect`) that are stage markers, not dedup verdicts. Introducing a second concept for one more state of the same kind doubles the operator's vocabulary for no practical gain.

- **TTL / profile-version-tag on `classified_in_domain`.** Rejected for v1: `mark_off_domain`, `mark_kept`, `mark_enrich_failed`, `mark_external_redirect` all have no TTL today, and profile drift would invalidate `off_domain` marks just as much as `classified_in_domain` ones. Adding TTL/versioning for one new state but not the existing four would be inconsistent; doing it for all five is a separate change with its own ADR if profile drift becomes a real operational pain.

- **Carry classify-stage state in working memory only and have judge in-call retry.** Rejected: doesn't survive process exit (crash, kill -9, machine reboot), and re-introduces in-call retry that ADR-0016 rejected.

## Consequences

- The **Deduplication Store** exposes a fifth narrow `mark_*` method (e.g. `mark_classified_in_domain`) and persists a fifth on-disk `status` value (`"classified_in_domain"`). The four existing `mark_*` methods and statuses are unchanged.
- `SeenResult` becomes a 4-variant `Literal["url_hit", "tuple_hit", "judge_pending", "miss"]`. `url_hit` and `tuple_hit` continue to mean "skip"; `judge_pending` means "enrich and route directly to the judge queue"; `miss` means "process from scratch." The orchestrator branches accordingly.
- The classify worker calls `mark_classified_in_domain(stub)` for each in-domain verdict *before* enqueuing the judge job. Mirror of the existing `mark_off_domain` call for the off-domain branch (`orchestrator.py:344-354`).
- The judge worker's existing `mark_kept(stub)` call overwrites `classified_in_domain` on success. `mark_*` semantics remain last-writer-wins; no separate transition method is introduced.
- The judge worker's failure path is unchanged: a raised `ExtractorError` still does not transition the state, so the URL remains `classified_in_domain` and the next run re-routes it.
- The alias-write logic (ADR-0004) is unchanged. If a `classified_in_domain` URL re-appears under a syndicated URL on a later run, the tuple tier copies the original record's `status` and `first_seen` to the new URL — the rerouting path then fires under the new URL as well.
- The **Run Divider** gains `judge_resumed=<n>` (conditional, omitted when zero), orthogonal to `degraded_reason` and the `*_abandoned` counters.
- `.seen.json` schema is unchanged in shape; the only difference is one new value in the `status` field's effective enum. Existing records remain readable; older runs without the new state simply produce zero `judge_resumed` events.
- ADR-0016's "raise → don't mark seen → next cron tick re-pays" rule continues to apply to *classify-stage* failures and to *enrich-stage* failures via `mark_enrich_failed`. Only the *judge-stage* boundary becomes resumable.
