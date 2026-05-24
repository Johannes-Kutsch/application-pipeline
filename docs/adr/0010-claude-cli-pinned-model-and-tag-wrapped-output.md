# Claude CLI: pinned model + tag-wrapped output

`ClaudeExtractor` pins `--model` (and `--effort` where applicable) per call site. Every `claude -p` response carries a structured payload wrapped in a semantic XML tag — `<verdict>` for the classifier, `<verdicts>` for the judge (since ADR-0014 collapsed judge to top-N).

Model/effort are module-level constants (`_CLASSIFY_MODEL = "haiku"`, `_JUDGE_MODEL = "haiku"`, `_JUDGE_EFFORT = "medium"`), not `Config` fields.

A project-agnostic **Agent Output Protocol** module extracts the payload via tag-anchored walk-back + regex fence-strip, then `json.loads`.

## Why

- Incident #240: CLI orchestration changes broke parsing. `--model` makes the responder deterministic.
- Tag-anchored parsing is durable — any preamble or fence doesn't kill extraction. Fence-strip is recovery, not prompt instruction.
- Bare aliases (`haiku`, `sonnet`) so point-release renames are transparent.

## Forensics taxonomy

`ClaudeCliError.envelope_error_class` ∈ `{envelope_not_json, envelope_not_object, cli_nonzero_exit, empty_result, tag_missing, json_malformed}`.

- `ClaudeCliError` — transient; next run retries.
- `ClaudeUsageLimitError` — carries parsed `reset_time` (ADR-0016).
- `ClaudeMalformedEnvelopeError` — crash-class.
- `tag_missing` / `json_malformed` — prompt-side or model-capability fix.

## Consequences

- `ClaudeCliInvoker.call()` takes `model: str` (required) and `effort: str = ""`. Returns `ClaudeResponse` without `parsed_result`.
- Module `application_pipeline/llm/agent_output.py` exports `extract_json_block(text, tag)` and `AgentOutputProtocolError`.
- Prompt templates close with tag instruction + rendered example per call site.
