# Classify is batched single-turn; classify + judge run as workers; usage-limit handling

The **Relevance Classifier** sends one Claude request per batch of up to `claude_classify_batch_size` (default 100) **Positions**, with each item carrying a stable string `id` and the response shape `[{"id": "...", "in_domain": bool, "extract": {...} | absent}, ...]` (extract added by ADR-0022). The **Match Judge** is one Claude request per run (per ADR-0020), not per item. Each Claude invocation is a fresh `claude -p` subprocess with no `--session-id` reuse — system-prompt savings come from Anthropic's prompt-cache TTL (5 min), not from a long-lived session.

The **Pipeline Orchestrator** may run classify on a dedicated worker thread (pipelined with parsers) or serially after the parser phase — implementation is free either way under the once-per-day cadence (ADR-0024) which removes wall-clock pressure. Where classify is threaded, `.seen.json` is the only artifact with genuinely concurrent writers, guarded by a single `threading.Lock` inside `DeduplicationStore`.

This supersedes ADR-0016/ADR-0017 (degraded-success exit on usage-limit; per-language buffer split; per-item judge call). The current usage-limit behaviour is sleep-and-retry per ADR-0023.

## Why

- **Classify is the volume site; judge is not.** Classify runs on every prefilter survivor; with the pool model (ADR-0022), the judge is one end-of-run call regardless of pool size. Batching pays where volume is and avoids the inflated payloads of batched judge responses.
- **Single-turn array, not multi-turn conversation.** "Batching" here is one prompt, N listings in, N verdicts out — not N turns inside a session. Multi-turn would pay growing prefix costs for stateless decisions.
- **ID-keyed request/response.** Extra output tokens per item are noise next to the system prompt; length-mismatch / dropped-item / reordered-item bugs become loud.
- **Fail the whole batch on malformed output.** If JSON parse fails, IDs don't match, or count is wrong: log the failure to `llm_classify_relevance.events.jsonl`, write the full prompt+response to `llm_classify_relevance.transcripts.jsonl`, and **do not mark any item in the batch seen**. Next cron tick retries. No per-item scalar fallback path.
- **Fresh session per batch.** Prompt-cache TTL keeps the system prompt cheap to re-send; tying batches into a conversation grows history we don't need.
- **Single-language pipeline.** ADR-0016 collapsed per-language buffers; one prompt per call site, German output regardless of input language.
- **DeduplicationStore lock.** Classify worker (and judge worker when present) write `mark_*` concurrently with the main thread. `is_seen`/`mark_*` acquire a `threading.Lock`; per-call overhead is a few hundred ns.

## Considered alternatives

- **Positional batching (no ids).** Rejected: silent verdict-to-listing misalignment is much worse than the few-extra-tokens cost.
- **Per-item scalar fallback after batch failure.** Rejected: duplicates the prompt and Protocol surface for a low-frequency case.
- **Long-lived session across batches.** Rejected: history grows linearly; cache TTL already covers the savings.
- **Batch the judge.** Rejected: blast radius of malformed judge output is worse.
- **Size-or-time flush trigger.** Rejected: bounded inflow already prevents indefinite buffering.

## Consequences

- **`LLMExtractor` Protocol**: `classify_relevance_batch(items: list[ClassifyItem]) -> (list[RelevanceVerdict], CallUsage)` and `judge_top_n(candidates: list[JudgeCandidate]) -> (list[MatchVerdict], CallUsage)`. The scalar `classify_relevance` and per-item `judge_match` methods are removed (per ADR-0020/0022).
- **Prompt files use `{{ITEMS}}`** for the rendered numbered list inside the single hardcoded prompt per call site (per ADR-0016). One prompt file per call site, German.
- **`Config` field**: `claude_classify_batch_size: int` (default 100). No code-side ceiling — stupid values diagnosed at first run.
- **Per-call-site log streams** (per ADR-0018): `llm_classify_relevance.{events,transcripts}.jsonl` and `llm_judge_match.{events,transcripts}.jsonl`. Each call (success and failure) produces an events row; failures and configurable successes write transcript rows with `ts, language, prompt, response, parsed, usage, cost_usd, duration_s, status, item_ids/stub_urls`.
- **Classify/judge thread model is implementation-free.** Either uses the same `DeduplicationStore` lock; either races against parser-thread `mark_enrich_failed`/`mark_external_redirect`.
- **Usage-limit handling**: see ADR-0023 (parse 429 reset time, sleep through it, retry). No `degraded_reason` field.
- **End-of-run row** lands in `pipeline_orchestrator.events.jsonl` as `event=run_complete` with per-call-site classify/judge token+cost counters (per ADR-0018 + ADR-0021).
