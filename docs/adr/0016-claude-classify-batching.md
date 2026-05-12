# Claude classify is batched single-turn; judge stays scalar

The **Relevance Classifier** sends one Claude request per language-grouped batch of up to `claude_classify_batch_size` (default 100) **Positions**, with each item carrying a stable string `id` and the response shape `[{"id": "...", "in_domain": bool}, ...]`. The **Match Judge** stays scalar — one Claude request per surviving in-domain **Position**. Each Claude invocation is a fresh `claude -p` subprocess with no `--session-id` / `--continue`; the system-prompt savings come from Anthropic's prompt-caching TTL, not from a long-lived Claude Code session.

## Why

- **Classify is the high-volume site; judge is not.** Classify runs on every **Position** that survives the **Domain Pre-Filter** (likely thousands per first run, dozens on steady state). Judge runs only on the in-domain subset. Batching pays where the volume is and avoids extending the judge response payload (`summary` + `matched` + `missing` × N), which is where bad output is most expensive.
- **Single-turn array, not multi-turn conversation.** "Batching" here means one prompt, N listings in, N verdicts out — not N turns inside a session. Multi-turn would pay growing prefix costs and isn't useful when each classify decision is stateless.
- **ID-keyed request/response, not positional.** Each `ClassifyItem` carries an opaque string id; the model returns `[{"id":..., "in_domain":...}, ...]`. The handful of extra output tokens per item is noise next to the system prompt, and length-mismatch / dropped-item / reordered-item bugs become loud rather than silent.
- **Fail the whole batch on malformed output.** If JSON parse fails, IDs don't match, or the model returns the wrong count, the orchestrator logs the failure to `synched/logs/llm.md` (per the [ADR-0012](0012-failures-as-syncthing-files.md) per-component log convention) and **does not mark any item in the batch seen**. Next cron tick retries those items. Matches the existing "Ollama raised → don't mark seen" semantics. No per-item scalar fallback path — same "poison-listing" hazard ADR-0001 already accepts.
- **Fresh session per batch.** No `--session-id` reuse across batches or runs. Anthropic's prompt-caching TTL (5 minutes) is what makes the system prompt cheap to re-send; tying batches into a conversation grows history we don't need and complicates abort recovery. Cache-read tokens are recorded in the **Run Divider** so we can verify the cache is actually firing.
- **Pipelining: classify → judge surviving items → next classify.** As soon as a batch reaches `claude_classify_batch_size` items (or all parsers finish, for the final undersized flush), the **Pipeline Orchestrator** sends the classify request, then iterates surviving in-domain items judging each one and appending+fsync+mark-seen before pulling more stubs. This minimizes work-in-flight when a usage-limit abort fires: at most one batch's worth of items roll into the next run unmarked. Off-domain items are marked seen immediately after classify returns false.
- **Size-only batch trigger (plus end-of-run flush).** No time-based flush. The whole pipeline is bounded by `SourceEntry.max_results × |SOURCES|`; the buffer cannot sit forever, and end-of-run flush handles "fewer than N items total" naturally. A slow parser delays classify of items already produced by fast parsers — acceptable; classify is one round-trip per batch and we don't lose listings, only ordering.
- **Batches are single-language by construction.** Per [ADR-0006](0006-per-language-prompt-files.md), the prompt file is selected by language; mixing languages in one batch is meaningless. The orchestrator maintains one buffer per `de`/`en`; `other` and `unknown` Positions fall back to the English buffer (per [ADR-0001](0001-local-ollama-as-llm-backend.md) — Claude is cross-lingually strong enough for the binary `in_domain` call).

## Considered alternatives

- **Positional batching (no ids)** — rejected: silent verdict-to-listing misalignment is much worse than the few-extra-tokens cost of ids.
- **Per-item scalar fallback after batch failure** — rejected: duplicates the prompt and Protocol surface for a low-frequency case; next cron tick retries are fine.
- **Long-lived session across batches** (`--session-id classify-<run>`) — rejected: history grows linearly, per-batch cost climbs, and the cache TTL already covers the savings we wanted.
- **Persistent session across runs** (`--continue`) — rejected: context window collapse over time, and session state becomes an additional artifact to manage.
- **Batch both classify and judge** — rejected: judge volume is too small to amortize, and the per-batch blast radius of malformed judge output is worse (richer output, more useful per item).
- **Size-or-time flush trigger** — rejected: introduces a tunable T and a timer for a problem the bounded `max_results` already prevents.
- **One prompt file per language, plus a separate per-item template file** — rejected: over-engineered for two prompt files. The item-list framing lives in the single prompt file alongside the **Triage Profile** so the user can tune both in one place.
- **`other` / `unknown` get their own prompt files and buffers** — rejected: tiny batches lose the cache amortization that motivates batching at all. English-instruction prompt is good enough for binary classify.

## Consequences

- **`LLMExtractor` Protocol grows an asymmetric surface.** `classify_relevance_batch(language, items: list[ClassifyItem]) -> list[RelevanceVerdict]` joins `judge_match(language, raw_description) -> MatchVerdict`. The scalar `classify_relevance` method is removed — classify is batch-only. The asymmetry is honest about the shape of the work.
- **Prompt files use `{{ITEMS}}` as the placeholder for the rendered numbered list** (matches the placeholder convention used in `pycastle/prompts/`). The prompt file owns how each item is framed (id + title + description block); code only substitutes the rendered string.
- **`Config` gains `claude_classify_batch_size: int` (default 100).** Tunable in `synched/config.py`. No code-side ceiling — user-error setting a stupid value is diagnosed at first run.
- **Run Divider grows new fields**: `classify_items` (item count, no longer equal to `classify_calls`), `claude_input_tokens`, `claude_output_tokens`, `claude_cache_read_tokens`, `claude_cost_usd` (summed across both call sites). The line gets longer; it is human-read, not parsed.
- **Per-component log file `synched/logs/llm.md`** receives batch failures, usage-limit aborts, and any envelope-level errors per the ADR-0012 extension.
- **Two prompt files only**: `classify_relevance.de.md`, `classify_relevance.en.md` (and the existing `judge_match.{de,en}.md`). `other`/`unknown` route to the English prompt.
- **Usage-limit error mid-batch aborts the run.** The orchestrator writes `synched/failures/<ts>.md`, exits non-zero, and the next cron tick retries. No `limit-until.txt` cooldown marker in v1.
