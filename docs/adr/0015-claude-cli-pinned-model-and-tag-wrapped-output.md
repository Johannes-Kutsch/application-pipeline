# Claude CLI: pinned model + tag-wrapped output

`ClaudeExtractor` pins `--model` (and `--effort` where applicable) per call site, and treats every `claude -p` response as agent prose with a structured payload wrapped in a semantic XML tag — `<verdicts>` for the **Relevance Classifier**, `<verdict>` for the **Match Judge** (`<verdicts>` since ADR-0020 collapsed judge to top-N).

Model/effort live as `ClaudeExtractor` module-level constants — `_CLASSIFY_MODEL = "haiku"` (no `--effort`), `_JUDGE_MODEL = "haiku"`, `_JUDGE_EFFORT = "medium"` — not as fields on user-editable `Config`. (Judge was pinned to `sonnet` in this ADR's first incarnation; moved to `haiku` for cost — see Why.)

A small project-agnostic **Agent Output Protocol** module extracts the payload via tag-anchored walk-back and regex fence-strip, then `json.loads`.

## Why

- **Incident #240: default-CLI orchestration changes broke parsing.** The CLI began routing through a Sonnet stage that wrapped output in ` ```json … ``` `; `json.loads` died at char 0. `--model` makes the responder deterministic across future CLI updates.
- **Per-call-site reasoning load drives the choice.** Classify is yes/no over 100 items — Haiku, no `--effort` (extended thinking is wasted on a binary task). Judge is open-vocabulary ranking + structured matched/missing + 2–3 sentence summary — initially Sonnet with `--effort medium`. After the token-cost grilling (issue #319) the judge moved to Haiku to capture the dominant cost lever (judge per-call cost ~15× classify); `--effort medium` was kept so a quality regression after the model-class step is attributable. Dropping `--effort` is a clean follow-up if quality holds.
- **Bare aliases over dated IDs.** `haiku`, `sonnet` so point-release renames are transparent.
- **Tag-anchored parsing is the durable defense.** Telling the model "respond with JSON" anchors parsing at character 0 — any preamble or fence kills it. A semantic tag gives both ends a named handle: find rightmost `</verdicts>`, walk back through `<verdicts>` openers until one parses; strip an optional surrounding fence inside each candidate. Stray substrings inside the JSON payload don't derail extraction.
- **Fence-strip is recovery, not prompt instruction.** Negative instructions to the model ("don't wrap in fences") are unreliable across model updates.
- **The Agent Output Protocol module is project-agnostic.** Exports `extract_json_block(text, tag)` and `AgentOutputProtocolError`. Imports nothing from project domain types. Call-site validation (id/length-match for `RelevanceVerdict`; field shape for `MatchVerdict`) stays in `ClaudeExtractor`.
- **Invoker stays dumb; extractor owns the parse pipeline.** `ClaudeCliInvoker.call()` does subprocess + envelope-shape validation + exit-code interpretation; returns `ClaudeResponse(raw_response, usage, cost_usd, duration_s, session_id)`. `ClaudeExtractor` calls `extract_json_block` then validates.
- **Surface model/effort to `Config`?** Rejected — conflates search-shape (Syncthing-edited, user-driven) with call-site engineering (code change, PR review). Silent quality/cost regressions are real.

## Forensics taxonomy

`ClaudeCliError.envelope_error_class` ∈ `{envelope_not_json, envelope_not_object, cli_nonzero_exit, empty_result, tag_missing, json_malformed}`. Six values, each pointing at a distinct fix path.

- **`ClaudeCliError`** (transient bucket): non-zero exit, empty `result`, or `result==""` with `is_error=False`. "This call didn't work; next run retries." No in-process retry (per ADR-0014's call-site convention).
- **`ClaudeUsageLimitError`**: triggered on the existing `is_error=True + result mentions limit` shape *and* on stderr matching the same phrases. Carries the parsed `reset_time` (see ADR-0023).
- **`ClaudeMalformedEnvelopeError`**: stdout isn't valid JSON, or the parsed envelope isn't a JSON object. Crash-class.
- **`tag_missing`**: model didn't follow output instructions (prompt-side fix).
- **`json_malformed`**: model emitted the tag but produced garbage inside (model-capability/truncation fix).

`ClaudeExtractor` writes a failure transcript line (status, full prompt, full stdout, full stderr, returncode, envelope dict, `envelope_error_class`) before re-raising as an `ExtractorError`.

## Consequences

- `ClaudeCliInvoker.call()` takes `model: str` (required kwarg) and `effort: str = ""` (empty = omit flag). Returns `ClaudeResponse` without a `parsed_result` field.
- New module `application_pipeline/llm/agent_output.py` exporting `extract_json_block(text, tag) -> Any` and `AgentOutputProtocolError(kind: "tag_missing" | "json_malformed")`. Standalone-tested against fixture strings.
- Prompt templates close with the tag instruction + one rendered example per call site. **Triage Profile** prose is preserved verbatim to keep the cache prefix stable.
- `AgentOutputProtocolError` is caught at the **LLM Extractor** boundary and re-raised as `ExtractorBatchMalformedError` / `ExtractorMalformedJSONError`, so the orchestrator's "don't mark seen, retry next run" path fires unchanged.
- **Cost impact of judge=haiku**: judge per-call cost drops ~10×. Verdict quality is monitored via the **Daily Top-5** content; reverting is a one-line constant change.
