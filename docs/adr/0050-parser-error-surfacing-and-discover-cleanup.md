# Parser error surfacing — Failure Reports from dead parsers; retire `max_results`; remove in-parser dedup; missing title is a failure

Four related changes to parser discover behaviour and error surfacing, unified by the principle: parser-level problems must be visible to the operator, and parsers should not duplicate responsibilities the orchestrator already owns.

## Changes

### 1. `_handle_parser_dead` writes a Failure Report

When the orchestrator receives a `_ParserDead` signal (parser thread died from an uncaught exception), it writes a **Failure Report** to `<settings-dir>/.runtime-data/failures/`. Today it only logs the traceback to `run.log` and increments `parsers_dead`. The Failure Report must include enough context for the operator to diagnose the problem without reading `run.log`: which parser, what URL was being called, the HTTP status or exception type, and the traceback. Each failure type must be distinguishable at a glance.

### 2. Retire `max_results`

The `max_results` field is removed from `SourceEntry`, `ParserQuery`, and the `ParserQuery` validation. Parsers paginate until the source returns an empty page. The per-source config knob and the `count >= query.max_results` early-return in each parser's discover loop are deleted.

The concern that broad keywords might return unbounded results is accepted: discover falloff is controlled by the source's own result set, and dedup short-circuits downstream processing for the vast majority of re-discovered URLs on steady-state runs.

### 3. Remove in-parser reference-number dedup

The `seen: set[str]` inside Bundesagentur's `discover()` — which tracked `referenznummer` values to skip duplicates across pages — is removed. The **Deduplication Store** already handles URL-based and tuple-based dedup downstream. Since the URL is constructed from the reference number, a duplicate ref produces a duplicate URL, and the store catches it. The in-parser dedup was a premature optimisation that duplicated a responsibility the store owns.

### 4. Missing title in discover writes a Failure Report

When a Bundesagentur search result item has no `stellenangebotsTitel`, the parser currently logs a `missing_title` event and silently skips the item. This is changed to write a **Failure Report** so the operator can investigate whether the API schema changed. The stub is still skipped (a titleless stub is useless downstream — the **Domain Pre-Filter** matches on titles, and the **Relevance Classifier** needs a title for context).

## Why

- **Operator visibility.** A dead parser, a changed API schema, or a missing title are all signals that something in the external world changed. Logging to `run.log` is insufficient — the operator doesn't read `run.log` on routine runs. Failure Reports are files the operator must acknowledge by deleting, matching the existing pattern (ADR-0010).
- **`max_results` is artificial friction.** The default of 1000 per source is high enough to never trigger for most queries, and low enough to silently truncate results for broad keywords. Removing it simplifies config, parser code, and the `ParserQuery` type. The dedup store absorbs the cost of large discover sets on steady-state runs.
- **Single responsibility for dedup.** The **Deduplication Store** is the authoritative dedup layer. In-parser dedup sets duplicate its logic, add state to parser threads (which should be pure producers per ADR-0005), and create a false sense that downstream dedup can be relaxed.

## Considered alternatives

- **Failure Report for missing title: log at INFO instead, escalate only if count exceeds a threshold.** Rejected for now: the user accepts the risk of noise and will tune down to INFO if missing titles turn out to be common in practice.
- **Keep `max_results` as a safety valve but raise the default to 10,000.** Rejected: an arbitrary cap doesn't solve the problem it claims to solve. If a source truly returns 10,001 results, the cap silently drops the last one. Either paginate to exhaustion or don't paginate at all.
- **Keep in-parser dedup as a network optimisation (avoids yielding stubs that will be immediately deduped).** Rejected: the network cost of yielding a duplicate stub to the orchestrator is negligible (it's an in-process queue put, not an HTTP call). The dedup store's URL-tier check is a dict lookup. The optimisation saves nothing measurable and duplicates a responsibility.

## Consequences

- **`_handle_parser_dead` in the orchestrator** gains a `write_failure()` call with structured context: parser ID, the exception, and the traceback. The existing `run.log` traceback write is kept alongside.
- **`SourceEntry.max_results` deleted** from `config/types.py`. `ParserQuery.max_results` deleted from `parsers/types.py`. All three parsers' discover loops remove their `count >= query.max_results` early-return. The `max_results` config validation is removed.
- **`seen: set[str]` removed** from `BundesagenturParser.discover()`. The `count` variable is also removed (it was only used for the `max_results` check).
- **Missing-title handling** in `BundesagenturParser.discover()` changes from log-and-continue to write-Failure-Report-and-continue. The stub is still skipped.
- **CONTEXT.md updates**: `Source`/`Config` entries drop `max_results` references. `Parser` entry drops mention of in-parser dedup. `Pagination` entry updates to reflect exhaustion-based termination. `Failure Report` entry adds parser-dead and missing-title as triggers.

## Supersedes / amends

- **Amends ADR-0010**: Failure Reports gain two new triggers — parser-dead events and missing-title items.
- **Amends ADR-0047**: `ParserQuery` loses `max_results`; parsers paginate to source exhaustion.
- **Amends ADR-0005**: parser threads remain pure producers but lose their in-parser dedup state.
