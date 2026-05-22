# Content Gate drops empty `raw_description` post-enrich

A new deterministic stage, the **Content Gate**, runs after the **Freshness Gate** and before the **Relevance Classifier**. It drops a **Position** when `raw_description.strip() == ""`. Dropped Positions are **not** marked in `.seen.json` — the URL is re-discovered, re-enriched, and re-checked on the next run, giving the source a chance to publish the body later. The gate is structurally parallel to the existing **Domain Pre-Filter** and **Freshness Gate**: same `admit(position) -> bool` / `emit_run_complete()` shape, same per-position transcript + per-run aggregate event, same Status Display row pattern.

## Why

- **Empty body is a Position-level fact, not a parser-level one.** Any source can produce a `Position(raw_description="")`. The existing per-parser short-circuit in `bundesagentur_api.py:209-222` is already incomplete — it only fires when `externeURL` is set. Spreading defensive empty-body checks across parsers is the wrong shape; the rule belongs in one source-agnostic place.
- **The classifier cost is deterministic-zero-value.** Prompting the LLM with `Beschreibung:\n\n` cannot produce a meaningful verdict. The model either hallucinates from the title or refuses to emit a `<verdict>` and trips `tag_missing` in the **Agent Output Protocol** — both observed on 2026-05-22 19:42:44 for `arbeitsagentur.de/jobsuche/jobdetail/15835-00090300248003-S`.
- **Validate before policy.** Pre-Filter and Freshness are policy decisions ("we don't want this kind"); Content is a validity check ("this isn't a usable input"). Conventionally validity precedes policy. The exception here is that Content runs **after** Pre-Filter, not before, because Pre-Filter is a pure title-only blacklist (ADR-0019) and does not need the body. This ordering is correct only while Pre-Filter remains title-only — if it ever needs positive content signals, Content must move ahead.
- **No new dedup status.** Drop is transient — the body may fill in tomorrow. Persisting an `empty_body` status would set a precedent that every transient-error class needs a `SeenStatus` value; transcripts and the run-summary counter give all the visibility the operator needs.
- **Named for the bucket, not the rule.** "Content Gate" leaves room for future content-validity rules (minimum body length, body-language detection). `EmptyBodyGate` would have to be renamed on the first additional rule.
- **Open-ended reason enum, not boolean.** Transcript reason is `{passed, empty_body}` today; adding a new rule does not change the transcript schema.

## Considered alternatives

- **Fix the Bundesagentur parser's "no outbound URL + empty body" path locally.** Rejected: papers over the general case; the next parser to hit the same shape repeats the bug.
- **Extend the Domain Pre-Filter to also check body emptiness.** Rejected: violates ADR-0019's "pure title-only blacklist" invariant; muddies per-keyword analytics.
- **Extend the Freshness Gate to also check body emptiness.** Rejected: conflates two orthogonal reasons; transcript aggregate counts would have to grow a sub-key.
- **Inline empty-body check in the orchestrator dispatcher.** Rejected: bypasses the gate-protocol shape that Pre-Filter and Freshness already share; no transcript, no per-run aggregate, no Status Display row.
- **Defensive check in the classifier prompt** ("if Beschreibung is empty, emit `in_domain: false`"). Rejected: spends tokens; doesn't prevent the `tag_missing` failure mode (an LLM is free to refuse instead); the gate makes this branch unreachable anyway.
- **Persist drops as a new `empty_body` `SeenStatus`.** Rejected: forecloses the next-run re-try; sets the "every transient class is a status" precedent.

## Consequences

- **New module `content_gate.py`** with `ContentGate.admit(position) -> bool` and `emit_run_complete()`. Construction signature `ContentGate(*, metrics: RunMetrics, run_log: RunLog)`. Private helpers: `_is_empty_body(raw_description)` and `_ContentVerdict(passes, reason)`.
- **New log component `pipeline_content`** (per ADR-0018). Per-position transcript row to `pipeline_content.transcripts.jsonl`: `{url, title, source, passes, reason ∈ {passed, empty_body}, body_len}`. Per-run `event=run_complete` row to `pipeline_content.events.jsonl`: `{content_considered, content_passed, content_dropped_empty_body}`.
- **Pipeline order**: `discover → dedup → prefilter (title) → enrich → freshness → content → classify → judge → render`. The **Agent Row** for `pipeline_content` sits between `pipeline_freshness` and `llm_classify_relevance`.
- **`.seen.json` unchanged.** No new status value; no `mark_*` call on drop. `RunScopedDedup` already prevents within-run re-enrichment via Cartesian expansion.
- **`RunMetrics` gains** `content_considered`, `content_passed`, `content_dropped_empty_body` counters, surfaced in the run-summary log line.
- **Status Display gains** a `pipeline_content` row, body string formatted from the new counters.
- **Bundesagentur parser's existing `external_redirect` short-circuit** (with `externeURL` set, ADR-0013 detection path) is **untouched** — that path emits `ExternalRedirect`, not a `Position` with empty body, so it never reaches the Content Gate.
- **CONTEXT.md updates** in a follow-up slice: `Raw Description` entry corrected (was "Empty after normalization is allowed" — now contradicts the gate); new `Content Gate` glossary entry under "Filtering & scoring"; "Relationships" chain extended to include the new gate.
- **ADR-0013's rejected "wrapper-only Position with empty body" alternative** ("pays classifier cost for deterministic-zero-value outcome; pollutes `kept`/`off_domain` ratios") is exactly what this gate enforces at runtime — the parser-side rejection is now backed by a pipeline-wide guard.
