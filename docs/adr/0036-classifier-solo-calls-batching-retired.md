# Classifier runs solo `claude -p` calls per Position; batching retired

The **Relevance Classifier** sends one Claude request per **Position** — `LLMExtractor.classify_relevance(item: ClassifyItem) -> (RelevanceVerdict, CallUsage)`. No id field, no array protocol, no `claude_classify_batch_size`. The pipelined single classify worker shape from ADR-0014 survives; only the per-call payload changes (batch size 1 internal). The same identical prefix (system prompt + `{USER_INFO}` + `{skills}` + per-item instructions) ships on every call and amortises through Anthropic's automatic prompt cache (5-min TTL). The prompt instructs the model to short-circuit on the first strong out-of-domain signal — no deep analysis on listings that are obviously off-domain.

This supersedes ADR-0014's batched-single-turn protocol. The **Match Judge** is untouched: it remains one call per run on `JudgeCandidate` list (ADR-0020/0022).

## Why

- **Whole-batch blast radius.** Under ADR-0014, one malformed JSON record forfeits the entire batch's 100 verdicts until the next cron tick. With one item per call, a malformed response forfeits one item — bounded, finer-grained, naturally cleaner failure semantics.
- **Lost-in-the-middle attention.** A 100-item batch packs ~150k input tokens into a single call. Items mid-batch demonstrably get sloppier extracts than items at the edges. Solo calls give the model the full context window per listing.
- **JSON brittleness at 100 records.** Forcing the model to emit 100 well-formed records in one shot is the source of the batch-failure mode in the first place. One verdict per call is structurally easier and the **Agent Output Protocol** (ADR-0015) parses the single `<verdict>` tag identically to today's `<verdicts>`.
- **Prompt cache makes solo affordable.** Anthropic's automatic 5-min prompt cache discounts the prefix by ~90% on cache hits. With a pipelined serial worker processing listings within seconds of each other, ~all calls after the first are warm. The remaining cost differential vs batching is ~15% on input — an acceptable tax for the three robustness wins above.
- **Fail-fast on off-domain is a precision trade.** The prompt biases the model toward terminating early on obviously off-domain listings (legal, medical, blue-collar, etc.) without producing a full extract. Low-certainty borderline cases that get classified `out_of_domain` would not have made the **Daily Top-5** anyway, so the recall loss is acceptable.
- **No within-run retry on malformed.** Failures stay loud (`llm_classify_relevance.events.jsonl` + transcripts), but the next cron tick is a cheaper retry surface than an immediate re-fire.

## Considered alternatives

- **Smaller batches (e.g. 10).** Rejected: splits the difference — still pays whole-batch blast radius (10 verdicts lost on malformed), still suffers attention degradation, gains nothing the solo + prompt-cache combo doesn't already give. The middle path costs robustness without recovering cost.
- **Claude CLI session resume with `--fork-session`.** Rejected after investigation: headless `-p` mode does not support true session forking. `--resume` appends linearly to one transcript, so parallel branches would race. Prompt cache delivers the desired "pre-warmed prefix" semantics without session machinery.
- **Two-stage prompt (`<decision>` then `<extract>`).** Rejected: off-domain output is already ~20 tokens under the existing schema; a second tag adds parser surface (Agent Output Protocol expects one tag) for negligible savings.
- **Parallel solo worker pool (N>1 in flight).** Rejected on a daily-cron cadence: 17-min serial wall-clock is well within budget, parallelism increases 429 likelihood without latency benefit, and the dedup lock holds either way.
- **Keep batching as-is.** Rejected for the four reasons above; cost is the only axis batching wins on, and the margin is ~15% on a personal-scale pipeline.

## Consequences

- **`LLMExtractor` Protocol**: `classify_relevance(item: ClassifyItem) -> (RelevanceVerdict, CallUsage)` replaces `classify_relevance_batch`. `ClassifyItem` drops the `id` field; `RelevanceVerdict` unchanged in shape (`{in_domain, extract: StructuredExtract | None}`).
- **Prompt shape**: one hardcoded prompt file at `src/application_pipeline/templates/prompts/classify_relevance.md` carrying the fail-fast instruction. The `{{ITEMS}}` array placeholder from ADR-0014 is retired in favour of single-item slots (`{{TITLE}}`, `{{RAW_DESCRIPTION}}`). The response tag is `<verdict>` (singular).
- **`Config` field `claude_classify_batch_size` is retired.** Removed from `Config`, from `templates/config.py`, from `ConfigError` validation. Hosts with the field still set in their `config.py` will get a `ConfigError` at next run (frozen typed Config rejects unknown fields) — the operator deletes the line and re-runs. No graceful deprecation needed at personal-pipeline scale.
- **Classify worker shape unchanged.** Pipelined single worker thread pulling one item at a time from the queue, firing one `claude -p` subprocess, writing the verdict via `DeduplicationStore.mark_in_domain` / `mark_out_of_domain` under the existing per-store lock (ADR-0014's lock survives).
- **Logging**: every classify call (success and failure) writes one event row to `llm_classify_relevance.events.jsonl` and one transcript row to `llm_classify_relevance.transcripts.jsonl`. Volume scales from ~2 rows/day batched to ~200 rows/day solo; disk impact ≈ 1 MB/day, fine on Pi. The "configurable successes" knob from ADR-0014 is retired — transcripts are always written. Logs are not Syncthing-synced (ADR-0002 covers only `.seen.json` + Daily Results Files).
- **Quota handling (ADR-0023) unchanged in spirit**: `ClaudeUsageLimitError` causes the worker to sleep until reset+2min and retry the same solo call. The ADR-0023 prose was updated to replace "the same batch" with "the same call".
- **`Position` ordering observable through events.** With one events row per call, prompt-tuning evals can be run against the per-call transcripts without reconstructing batch boundaries.
- **No migration of `.seen.json`.** Stored statuses are payload-agnostic; the schema change is on the prompt/response wire only.
